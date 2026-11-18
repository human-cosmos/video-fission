from __future__ import annotations

import re
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any

from . import media
from .config import Settings
from .conversation_cards import cards_for, stable_item_id, upload_media_url
from .db import Database
from .paths import production_artifact_dir, user_artifact_root, user_upload_root, user_workspace
from .pipeline import PipelineEngine
from .replace import normalize_replace_policy, resolve_replace_plan
from .settings_store import SettingsStore

VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".m4v", ".qt"}
VIDEO_CONTENT_TYPES = {
    "video/mp4",
    "video/quicktime",
    "video/webm",
    "video/x-m4v",
}
MAX_PRODUCTION_UPLOAD_BYTES = 500 * 1024 * 1024
ID_RE = re.compile(r"^prd_[a-f0-9]{16,32}$")
UPLOAD_ID_RE = re.compile(r"^upl_[a-f0-9]{16,32}$")


class ProductionConflict(RuntimeError):
    pass


class ProductionNotFound(RuntimeError):
    pass


class ProductionService:
    def __init__(self, settings: Settings, db: Database, store: SettingsStore):
        self.settings = settings
        self.db = db
        self.store = store
        self.engine = PipelineEngine(settings, db, store)
        self.engine.on_after_emit = self.sync_conversation_cards
        self._lock = threading.Lock()
        self._cancels: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}

    def save_upload(
        self,
        filename: str,
        content_type: str,
        data_iter,
        size_hint: int | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        original = Path(filename or "reference.mp4").name
        suffix = Path(original).suffix.lower()[:12] or ".mp4"
        if suffix not in VIDEO_SUFFIXES and (content_type or "") not in VIDEO_CONTENT_TYPES:
            raise ValueError("只接受 mp4 / mov / webm / m4v 参考视频")
        upload_id = f"upl_{uuid.uuid4().hex[:24]}"
        dest_dir = user_upload_root(self.settings.uploads_dir, user_id, production=True)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{upload_id}{suffix}"
        total = 0
        with dest.open("wb") as handle:
            for chunk in data_iter:
                total += len(chunk)
                if total > MAX_PRODUCTION_UPLOAD_BYTES:
                    handle.close()
                    dest.unlink(missing_ok=True)
                    raise ValueError("文件不能超过 500MB")
                handle.write(chunk)
        try:
            info = media.probe(dest)
        except media.MediaError as exc:
            dest.unlink(missing_ok=True)
            raise ValueError(str(exc)) from exc
        record = self.db.create_video_upload(
            {
                "id": upload_id,
                "user_id": user_id,
                "name": original,
                "path": str(dest.resolve()),
                "size": total,
                "content_type": content_type or "video/mp4",
                **info,
            }
        )
        return {
            "id": record["id"],
            "name": record["name"],
            "path": record["path"],
            "size": record["size"],
            "content_type": record["content_type"],
            "duration_ms": record["duration_ms"],
            "width": record["width"],
            "height": record["height"],
            "fps": record["fps"],
            "url": upload_media_url(record["id"]),
        }

    def create(
        self,
        title: str | None,
        brief: str,
        video_upload_id: str,
        replace_policy: dict[str, Any] | None = None,
        conversation_id: str | None = None,
        source_item_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        brief = (brief or "").strip()
        if not brief:
            raise ValueError("brief 必填")
        if len(brief) > 20_000:
            raise ValueError("brief 最长 20000 字")
        upload = self.db.get_video_upload(video_upload_id, user_id)
        if not upload:
            raise ValueError("参考视频不存在")
        production_id = f"prd_{uuid.uuid4().hex[:24]}"
        title_text = (title or "").strip() or brief.splitlines()[0][:40]
        source_video = {
            "upload_id": upload["id"],
            "filename": upload["name"],
            "path": upload["path"],
            "duration_ms": upload["duration_ms"],
            "width": upload["width"],
            "height": upload["height"],
            "fps": upload["fps"],
        }
        bound_conversation = (conversation_id or "").strip() or None
        if bound_conversation and not self.db.get_conversation(bound_conversation, user_id):
            raise ValueError("对话不存在")
        self.db.create_production(
            {
                "id": production_id,
                "user_id": user_id,
                "title": title_text,
                "brief": brief,
                "status": "draft",
                "current_stage": "ingest",
                "error": None,
                "warnings": [],
                "source_video": source_video,
                "decomposition": None,
                "intent": None,
                "skeleton": None,
                "script": None,
                "clips": [],
                "output": None,
                "replace_policy": normalize_replace_policy(replace_policy),
                "replace_plan": None,
                "video_upload_id": upload["id"],
                "conversation_id": bound_conversation,
                "source_item_id": (source_item_id or "").strip() or None,
            }
        )
        public = self.public(production_id, user_id=user_id)
        self.sync_conversation_cards(production_id)
        return public

    def patch(
        self,
        production_id: str,
        title: str | None = None,
        brief: str | None = None,
        replace_policy: dict[str, Any] | None = None,
        conversation_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        production = self.db.get_production(production_id, user_id)
        if not production:
            raise ProductionNotFound("创作任务不存在")
        if production["status"] == "running":
            raise ProductionConflict("任务正在运行")
        updates: dict[str, Any] = {}
        if title is not None:
            title_text = title.strip()
            if not title_text:
                raise ValueError("title 不能为空")
            if len(title_text) > 120:
                raise ValueError("title 最长 120 字")
            updates["title"] = title_text
        if brief is not None:
            brief_text = brief.strip()
            if not brief_text:
                raise ValueError("brief 必填")
            if len(brief_text) > 20_000:
                raise ValueError("brief 最长 20000 字")
            updates["brief"] = brief_text
        if replace_policy is not None:
            updates["replace_policy"] = normalize_replace_policy(replace_policy)
        if conversation_id is not None:
            current = (production.get("conversation_id") or "").strip() or None
            desired = conversation_id.strip() or None
            if current and current != desired:
                raise ProductionConflict("已绑定的创作任务不能改到另一个对话")
            if desired:
                if not self.db.get_conversation(desired, user_id):
                    raise ValueError("对话不存在")
                updates["conversation_id"] = desired
        if updates:
            self.db.update_production(production_id, updates)
        public = self.public(production_id, user_id=user_id)
        if "conversation_id" in updates:
            self.sync_conversation_cards(production_id)
        return public

    def get(self, production_id: str, user_id: str | None = None) -> dict[str, Any]:
        production = self.db.get_production(production_id, user_id)
        if not production:
            raise ProductionNotFound("创作任务不存在")
        return self._to_public(production)

    def list_items(
        self,
        conversation_id: str | None = None,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        items = []
        for production in self.db.list_productions(conversation_id=conversation_id, user_id=user_id):
            public = self._to_public(production)
            items.append(
                {
                    "id": public["id"],
                    "title": public["title"],
                    "brief": public["brief"],
                    "status": public["status"],
                    "current_stage": public["current_stage"],
                    "error": public["error"],
                    "output": public.get("output"),
                    "conversation_id": public.get("conversation_id"),
                    "created_at": public["created_at"],
                    "updated_at": public["updated_at"],
                }
            )
        return items

    def public(self, production_id: str, user_id: str | None = None) -> dict[str, Any]:
        return self.get(production_id, user_id=user_id)

    def run(
        self,
        production_id: str,
        wait: bool = False,
        replace_policy: dict[str, Any] | None = None,
        resume_from: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        production = self.db.get_production(production_id, user_id)
        if not production:
            raise ProductionNotFound("创作任务不存在")
        if production["status"] == "running":
            raise ProductionConflict("任务正在运行")
        updates: dict[str, Any] = {}
        if replace_policy is not None:
            updates["replace_policy"] = normalize_replace_policy(replace_policy)
        policy = normalize_replace_policy(updates.get("replace_policy") or production.get("replace_policy"))
        brief = production.get("brief") or ""
        segments = (production.get("decomposition") or {}).get("segments") or []
        resume = self._resolve_resume_from(production, resume_from, segments)
        if policy["mode"] == "selected" and not (
            policy["segment_ids"] or policy["roles"] or policy["time_ranges"]
        ):
            raise ValueError("没有命中任何片段")
        if segments:
            # Validate selected/brief hits before starting, so HTTP can return 400.
            plan = resolve_replace_plan(policy, segments, brief)
            updates["replace_plan"] = plan
        updates.update({"status": "running", "current_stage": resume or "ingest", "error": None})
        self.db.update_production(production_id, updates)
        self.sync_conversation_cards(production_id)
        cancel = threading.Event()
        with self._lock:
            self._cancels[production_id] = cancel
        if wait:
            try:
                self.engine.run(production_id, cancel, resume_from=resume)
            finally:
                with self._lock:
                    self._cancels.pop(production_id, None)
            return self.public(production_id, user_id=user_id)
        thread = threading.Thread(
            target=self._run_thread,
            args=(production_id, cancel, resume),
            name=f"production-{production_id}",
            daemon=True,
        )
        with self._lock:
            self._threads[production_id] = thread
        thread.start()
        return self.public(production_id, user_id=user_id)

    def _resolve_resume_from(
        self,
        production: dict[str, Any],
        resume_from: str | None,
        segments: list[dict[str, Any]],
    ) -> str | None:
        allowed = {"ingest", "decompose", "intent"}
        if resume_from:
            value = str(resume_from).strip()
            if value not in allowed:
                raise ValueError("resume_from 只能是 ingest|decompose|intent")
            if value == "intent" and not segments:
                raise ValueError("还没有拆解结果，无法从 intent 恢复")
            return value
        if segments:
            return "intent"
        return None

    def cancel(self, production_id: str, user_id: str | None = None) -> dict[str, Any]:
        production = self.db.get_production(production_id, user_id)
        if not production:
            raise ProductionNotFound("创作任务不存在")
        if production["status"] in {"completed", "failed"}:
            raise ProductionConflict("已结束的任务不能取消")
        with self._lock:
            event = self._cancels.get(production_id)
        if event:
            event.set()
        if production["status"] != "running":
            self.db.update_production(production_id, {"status": "cancelled"})
        public = self.public(production_id, user_id=user_id)
        self.sync_conversation_cards(production_id)
        return public

    def delete(self, production_id: str, user_id: str | None = None) -> None:
        production = self.db.get_production(production_id, user_id)
        if not production:
            raise ProductionNotFound("创作任务不存在")
        if production["status"] == "running":
            raise ProductionConflict("运行中的任务请先取消")
        upload_id = production.get("video_upload_id") or production.get("source_video", {}).get("upload_id")
        artifact_dir = production_artifact_dir(
            self.settings.artifacts_dir,
            production.get("user_id"),
            production_id,
        )
        legacy_artifact_dir = self.settings.artifacts_dir / "productions" / production_id
        self.db.delete_production(production_id)
        shutil.rmtree(artifact_dir, ignore_errors=True)
        if legacy_artifact_dir != artifact_dir:
            shutil.rmtree(legacy_artifact_dir, ignore_errors=True)
        if upload_id and self.db.count_productions_for_upload(upload_id) == 0:
            upload = self.db.get_video_upload(upload_id)
            if upload:
                Path(upload["path"]).unlink(missing_ok=True)
                self.db.delete_video_upload(upload_id)

    def events(
        self,
        production_id: str,
        after_id: int = 0,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if not self.db.get_production(production_id, user_id):
            raise ProductionNotFound("创作任务不存在")
        return self.db.list_production_events(
            production_id,
            after_id=after_id,
            user_id=user_id,
        )

    def artifact_path(
        self,
        production_id: str,
        name: str,
        user_id: str | None = None,
    ) -> Path:
        production = self.db.get_production(production_id, user_id)
        if not production:
            raise ProductionNotFound("创作任务不存在")
        if not name or name != Path(name).name or ".." in name or "/" in name or "\\" in name:
            raise ValueError("非法文件名")
        artifact_root = production_artifact_dir(
            self.settings.artifacts_dir,
            production.get("user_id"),
            production_id,
        )
        legacy_root = self.settings.artifacts_dir / "productions" / production_id
        if user_id and not (artifact_root / name).is_file() and (legacy_root / name).is_file():
            artifact_root = legacy_root
        path = (artifact_root / name).resolve()
        root = artifact_root.resolve()
        if root not in path.parents and path != root:
            raise ValueError("非法文件名")
        if not path.is_file():
            raise FileNotFoundError("产物不存在")
        return path

    def start_from_tool(
        self,
        brief: str,
        video_upload_id: str | None = None,
        source_path: str | None = None,
        replace_policy: dict[str, Any] | None = None,
        conversation_id: str | None = None,
        source_item_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        if not video_upload_id and source_path:
            video_upload_id = self._register_existing_path(source_path, user_id)
        if not video_upload_id:
            raise ValueError("需要 video_upload_id 或已上传文件路径")
        brief_text = (brief or "").strip()
        conversation = (conversation_id or "").strip() or None
        if source_item_id:
            existing = self.db.find_production_by_source_item(source_item_id, user_id)
            if existing:
                return self._to_public(existing)
        if conversation:
            existing = self.db.find_duplicate_production(
                conversation,
                video_upload_id,
                brief_text,
                user_id,
            )
            if existing:
                return self._to_public(existing)
        production = self.create(
            None,
            brief_text,
            video_upload_id,
            replace_policy=replace_policy,
            conversation_id=conversation,
            source_item_id=source_item_id,
            user_id=user_id,
        )
        return self.run(production["id"], user_id=user_id)

    def upload_media_path(
        self,
        upload_id: str,
        user_id: str | None = None,
    ) -> tuple[Path, dict[str, Any]]:
        if not UPLOAD_ID_RE.match(upload_id or ""):
            raise ValueError("非法上传标识")
        record = self.db.get_video_upload(upload_id, user_id)
        if not record:
            raise ProductionNotFound("参考视频不存在")
        path = Path(record["path"]).expanduser().resolve()
        root = user_upload_root(
            self.settings.uploads_dir,
            user_id,
            production=True,
        ).resolve()
        legacy_root = (self.settings.uploads_dir / "productions").resolve()
        allowed_roots = [root]
        if user_id:
            allowed_roots.append(legacy_root)
        if not any(allowed == path or allowed in path.parents for allowed in allowed_roots):
            raise ValueError("非法文件路径")
        if not path.is_file():
            raise FileNotFoundError("参考视频不存在")
        return path, record

    def sync_conversation_cards(
        self,
        production_id: str,
        kind: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        production = self.db.get_production(production_id)
        if not production:
            return
        conversation_id = (production.get("conversation_id") or "").strip()
        if not conversation_id or not self.db.get_conversation(conversation_id):
            return
        public = self._to_public(production)
        artifact_dir = production_artifact_dir(
            self.settings.artifacts_dir,
            production.get("user_id"),
            production_id,
        )
        legacy_artifact_dir = self.settings.artifacts_dir / "productions" / production_id
        if not artifact_dir.is_dir() and legacy_artifact_dir.is_dir():
            artifact_dir = legacy_artifact_dir
        last_item = None
        for item_type, card_payload in cards_for(public, artifact_dir if artifact_dir.is_dir() else None):
            last_item = self.db.upsert_item(
                conversation_id,
                stable_item_id(production_id, item_type),
                {
                    "item_type": item_type,
                    "role": "assistant",
                    "status": "completed",
                    "codex_item_id": None,
                    "payload": card_payload,
                },
            )
            self.db.append_event(
                conversation_id,
                "item_upserted",
                {
                    "item_id": last_item.get("id"),
                    "item_type": item_type,
                    "production_id": production_id,
                    "item": last_item,
                },
            )
        self.db.append_event(
            conversation_id,
            "production_updated",
            {
                "production_id": production_id,
                "status": public.get("status"),
                "current_stage": public.get("current_stage"),
                "kind": kind,
                "payload": payload or {},
            },
        )

    def _register_existing_path(self, source_path: str, user_id: str | None = None) -> str:
        path = Path(source_path).expanduser().resolve()
        # Allow workspace, uploads, artifacts, fixtures
        allowed_roots = [
            user_upload_root(self.settings.uploads_dir, user_id).resolve(),
            user_workspace(self.settings.workspace, user_id).resolve(),
            user_artifact_root(self.settings.artifacts_dir, user_id).resolve(),
            (self.settings.project_root / "backend" / "tests" / "fixtures").resolve(),
        ]
        if not any(root == path or root in path.parents for root in allowed_roots):
            raise ValueError("文件路径不在允许的目录内")
        if not path.is_file():
            raise ValueError("参考视频不存在")
        info = media.probe(path)
        upload_id = f"upl_{uuid.uuid4().hex[:24]}"
        dest_dir = user_upload_root(self.settings.uploads_dir, user_id, production=True)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{upload_id}{path.suffix or '.mp4'}"
        dest.write_bytes(path.read_bytes())
        self.db.create_video_upload(
            {
                "id": upload_id,
                "user_id": user_id,
                "name": path.name,
                "path": str(dest),
                "size": dest.stat().st_size,
                "content_type": "video/mp4",
                **info,
            }
        )
        return upload_id

    def _run_thread(self, production_id: str, cancel: threading.Event, resume_from: str | None = None) -> None:
        try:
            self.engine.run(production_id, cancel, resume_from=resume_from)
        except Exception:
            pass
        finally:
            with self._lock:
                self._cancels.pop(production_id, None)
                self._threads.pop(production_id, None)

    def _to_public(self, production: dict[str, Any]) -> dict[str, Any]:
        production_id = production["id"]
        output = production.get("output") or None
        if isinstance(output, dict):
            output = {
                **output,
                "video_url": output.get("video_url") or f"/api/productions/{production_id}/artifacts/final.mp4",
                "srt_url": output.get("srt_url") or (
                    f"/api/productions/{production_id}/artifacts/final.srt" if output.get("srt_path") else ""
                ),
            }
        return {
            "id": production["id"],
            "title": production["title"],
            "brief": production["brief"],
            "status": production["status"],
            "current_stage": production["current_stage"],
            "error": production["error"],
            "warnings": production.get("warnings") or [],
            "source_video": production.get("source_video") or {},
            "decomposition": production.get("decomposition"),
            "intent": production.get("intent"),
            "skeleton": production.get("skeleton"),
            "script": production.get("script"),
            "clips": production.get("clips") or [],
            "output": output,
            "replace_policy": production.get("replace_policy") or {
                "mode": "default",
                "roles": [],
                "segment_ids": [],
                "time_ranges": [],
            },
            "replace_plan": production.get("replace_plan"),
            "conversation_id": production.get("conversation_id"),
            "created_at": production["created_at"],
            "updated_at": production["updated_at"],
        }
