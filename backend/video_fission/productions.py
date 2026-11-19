from __future__ import annotations

import logging
import re
import shutil
import threading
import uuid
from contextvars import copy_context
from pathlib import Path
from typing import Any

from . import media
from .config import Settings
from .conversation_cards import VIDEO_CARD_TYPES, cards_for, stable_item_id, upload_media_url
from .db import Database
from .paths import production_artifact_dir, user_artifact_root, user_upload_root, user_workspace
from .pipeline import PipelineEngine
from .production_context import creative_context_paths
from .replace import normalize_replace_policy, resolve_replace_plan
from .script_flow import candidate_by_id, classify_followup
from .settings_store import SettingsStore

logger = logging.getLogger(__name__)

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
        self.agent = None
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
        reference_images: list[dict[str, Any]] | None = None,
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
                "candidate_videos": [],
                "clips": [],
                "output": None,
                "replace_policy": normalize_replace_policy(replace_policy),
                "replace_plan": None,
                "reference_images": list(reference_images or []),
                "video_upload_id": upload["id"],
                "conversation_id": bound_conversation,
                "source_item_id": (source_item_id or "").strip() or None,
            }
        )
        public = self.public(production_id, user_id=user_id)
        self.sync_conversation_cards(production_id)
        return public

    def update_creative_context(
        self,
        production_id: str,
        *,
        intent: dict[str, Any] | None = None,
        skeleton: dict[str, Any] | None = None,
        script_candidates: list[dict[str, Any]] | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        production = self.db.get_production(production_id, user_id)
        if not production:
            raise ProductionNotFound("创作任务不存在")
        if production.get("status") == "running":
            raise ProductionConflict("任务正在运行")
        updates: dict[str, Any] = {}
        if intent is not None:
            if not isinstance(intent, dict):
                raise ValueError("intent 必须是对象")
            updates["intent"] = intent
        if skeleton is not None:
            if not isinstance(skeleton, dict):
                raise ValueError("skeleton 必须是对象")
            updates["skeleton"] = skeleton
        if script_candidates is not None:
            if not isinstance(script_candidates, list) or len(script_candidates) != 3:
                raise ValueError("script_candidates 必须恰好包含 3 个脚本")
            ids = [
                str(item.get("id") or "").strip() if isinstance(item, dict) else ""
                for item in script_candidates
            ]
            if any(not item_id for item_id in ids) or len(set(ids)) != 3:
                raise ValueError("3 个脚本候选必须有互不相同的 id")
            updates["script_candidates"] = script_candidates
            updates["selected_script_id"] = None
            updates["candidate_videos"] = []
        if not updates:
            return self.public(production_id, user_id=user_id)
        self.db.update_production(production_id, updates)
        self.sync_conversation_cards(production_id)
        return self.public(production_id, user_id=user_id)

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
        with self._lock:
            active_thread = self._threads.get(production_id)
        if active_thread and active_thread.is_alive():
            raise ProductionConflict("任务正在停止，请稍后再试")
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

    def prepare(
        self,
        production_id: str,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        production = self.db.get_production(production_id, user_id)
        if not production:
            raise ProductionNotFound("创作任务不存在")
        if production.get("status") == "running":
            raise ProductionConflict("任务正在运行")
        self.db.update_production(
            production_id,
            {"status": "running", "current_stage": "ingest", "error": None},
        )
        self.sync_conversation_cards(production_id)
        cancel = threading.Event()
        with self._lock:
            self._cancels[production_id] = cancel
        try:
            logger.info("production.prepare_started")
            self._orchestrator().run(production_id, cancel, prepare_only=True)
        except Exception:
            logger.exception("production.prepare_failed")
            raise
        finally:
            with self._lock:
                self._cancels.pop(production_id, None)
            self.sync_conversation_cards(production_id)
        return self.public(production_id, user_id=user_id)

    def attach_conversation_reference_images(
        self,
        production_id: str,
        conversation_id: str | None,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        production = self.db.get_production(production_id, user_id)
        if not production:
            raise ProductionNotFound("创作任务不存在")
        conversation = str(conversation_id or "").strip()
        if not conversation:
            return list(production.get("reference_images") or [])
        items = self.db.list_items(conversation)
        files: list[dict[str, Any]] = []
        for item in reversed(items):
            if item.get("item_type") != "userMessage" or not isinstance(item.get("payload"), dict):
                continue
            candidate_files = item["payload"].get("files") or []
            if any(
                isinstance(file, dict)
                and (
                    file.get("kind") == "reference_image"
                    or str(file.get("content_type") or "").startswith("image/")
                )
                for file in candidate_files
            ):
                files = candidate_files
                break
        allowed_root = user_upload_root(self.settings.uploads_dir, user_id).resolve()
        production_root = user_upload_root(
            self.settings.uploads_dir,
            user_id,
            production=True,
        ).resolve()
        images: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in files:
            if not isinstance(item, dict):
                continue
            content_type = str(item.get("content_type") or "")
            name = str(item.get("name") or "")
            kind = str(item.get("kind") or "")
            if kind != "reference_image" and not content_type.startswith("image/"):
                continue
            path = Path(str(item.get("path") or "")).expanduser().resolve()
            if (
                not path.is_file()
                or not any(root == path or root in path.parents for root in (allowed_root, production_root))
                or str(path) in seen
            ):
                continue
            seen.add(str(path))
            images.append(
                {
                    "id": str(item.get("id") or path.stem),
                    "kind": "reference_image",
                    "name": name or path.name,
                    "path": str(path),
                    "size": int(item.get("size") or path.stat().st_size),
                    "content_type": content_type or "image/jpeg",
                    "url": str(item.get("url") or (f"/api/uploads/{path.stem}" if path.stem else "")),
                }
            )
            if len(images) >= 3:
                break
        if images:
            self.db.update_production(production_id, {"reference_images": images})
            self.sync_conversation_cards(production_id)
        return images or list(production.get("reference_images") or [])

    def generate_scripts_with_gemini(
        self,
        production_id: str,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        from .creative_generation import generate_creative_bundle

        production = self.db.get_production(production_id, user_id)
        if not production:
            raise ProductionNotFound("创作任务不存在")
        if production.get("status") == "running":
            raise ProductionConflict("任务正在运行")
        if not production.get("reference_images") and production.get("conversation_id"):
            self.attach_conversation_reference_images(
                production_id,
                production.get("conversation_id"),
                user_id=user_id,
            )
            production = self.db.get_production(production_id, user_id) or production
        bundle = generate_creative_bundle(self.store, production, user_id=user_id)
        host = getattr(self, "tool_host", None)
        if host is None:
            raise ValueError("创作工具尚未就绪")
        context = {
            "production_id": production_id,
            "conversation_id": production.get("conversation_id"),
        }
        host.lock_timeline_opening({"production_id": production_id}, context)
        host.write_intent_and_scripts(
            {
                "production_id": production_id,
                "intent": bundle["intent"],
                "skeleton": bundle["skeleton"],
                "script_candidates": bundle["script_candidates"],
            },
            context,
        )
        self.engine._emit(
            production_id,
            "awaiting_script_approval",
            {
                "message": "创意脚本已由 Gemini 生成，等待用户确认后再生视频",
                "model": bundle.get("model") or "",
            },
        )
        return self.public(production_id, user_id=user_id)

    def approve_scripts(
        self,
        production_id: str,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        production = self.db.get_production(production_id, user_id)
        if not production:
            raise ProductionNotFound("创作任务不存在")
        if production.get("status") != "awaiting_script_approval":
            raise ProductionConflict("当前任务不在脚本确认阶段")
        if len(production.get("script_candidates") or []) != 3:
            raise ValueError("还没有 3 个可确认的脚本候选")
        return self.run(
            production_id,
            resume_from="generate",
            user_approved=True,
            user_id=user_id,
        )

    def run(
        self,
        production_id: str,
        wait: bool = False,
        replace_policy: dict[str, Any] | None = None,
        resume_from: str | None = None,
        user_approved: bool = False,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        production = self.db.get_production(production_id, user_id)
        if not production:
            raise ProductionNotFound("创作任务不存在")
        if production["status"] == "running":
            raise ProductionConflict("任务正在运行")
        with self._lock:
            active_thread = self._threads.get(production_id)
        if active_thread and active_thread.is_alive():
            raise ProductionConflict("任务正在停止，请稍后再试")
        updates: dict[str, Any] = {}
        if replace_policy is not None:
            updates["replace_policy"] = normalize_replace_policy(replace_policy)
        policy = normalize_replace_policy(updates.get("replace_policy") or production.get("replace_policy"))
        brief = production.get("brief") or ""
        segments = (production.get("decomposition") or {}).get("segments") or []
        resume = self._resolve_resume_from(production, resume_from, segments)
        if (
            resume == "generate"
            and production.get("status") == "awaiting_script_approval"
            and not user_approved
        ):
            raise ValueError("创意脚本尚未确认，不能开始生成视频")
        if policy["mode"] == "selected" and not (policy["segment_ids"] or policy["roles"] or policy["time_ranges"]):
            raise ValueError("没有命中任何片段")
        if segments:
            # Validate selected/brief hits before starting, so HTTP can return 400.
            plan = resolve_replace_plan(
                policy,
                segments,
                brief,
                opening=(production.get("decomposition") or {}).get("opening"),
            )
            updates["replace_plan"] = plan
        stage = "compose" if resume == "finalize" else (resume or "ingest")
        updates.update({"status": "running", "current_stage": stage, "error": None})
        self.db.update_production(production_id, updates)
        self.sync_conversation_cards(production_id)
        cancel = threading.Event()
        with self._lock:
            self._cancels[production_id] = cancel
        if wait:
            try:
                logger.info("production.run_wait_started resume_from=%s", resume or "-")
                self._orchestrator().run(production_id, cancel, resume_from=resume)
            except Exception:
                logger.exception("production.run_wait_failed")
            finally:
                with self._lock:
                    self._cancels.pop(production_id, None)
            return self.public(production_id, user_id=user_id)
        context = copy_context()

        def run_pipeline_thread() -> None:
            context.run(self._run_thread, production_id, cancel, resume)

        thread = threading.Thread(
            target=run_pipeline_thread,
            name=f"production-{production_id}",
            daemon=True,
        )
        with self._lock:
            self._threads[production_id] = thread
        logger.info("production.run_queued resume_from=%s", resume or "-")
        thread.start()
        return self.public(production_id, user_id=user_id)

    def _resolve_resume_from(
        self,
        production: dict[str, Any],
        resume_from: str | None,
        segments: list[dict[str, Any]],
    ) -> str | None:
        allowed = {"ingest", "decompose", "intent", "script", "generate", "finalize"}
        if resume_from:
            value = str(resume_from).strip()
            if value not in allowed:
                raise ValueError("resume_from 只能是 ingest|decompose|intent|script|generate")
            if value in {"intent", "script", "generate"} and not segments:
                raise ValueError(f"还没有拆解结果，无法从 {value} 恢复")
            if value == "generate":
                if len(production.get("script_candidates") or []) != 3:
                    raise ValueError("还没有 3 个脚本候选，无法生成候选视频")
            if value == "finalize":
                if not production.get("selected_script_id"):
                    raise ValueError("还没有选定视频，无法继续最终合成")
                if not production.get("candidate_videos"):
                    raise ValueError("还没有可用于最终合成的候选视频")
            return value
        if not segments:
            return None
        _action, stage = classify_followup(production, production.get("brief") or "")
        if _action == "select":
            return "generate"
        return stage

    def select_script(
        self,
        production_id: str,
        script_id: str,
        wait: bool = False,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        production = self.db.get_production(production_id, user_id)
        if not production:
            raise ProductionNotFound("创作任务不存在")
        if production["status"] == "running":
            raise ProductionConflict("任务正在运行")
        chosen = candidate_by_id(production.get("script_candidates") or [], script_id)
        if not chosen:
            raise ValueError("脚本候选不存在")
        self.db.update_production(
            production_id,
            {
                "script": chosen.get("script") or production.get("script"),
                "selected_script_id": chosen["id"],
                "error": None,
            },
        )
        self.sync_conversation_cards(production_id)
        return self.run(
            production_id,
            wait=wait,
            resume_from="generate",
            user_approved=True,
            user_id=user_id,
        )

    def select_video(
        self,
        production_id: str,
        video_id: str,
        wait: bool = False,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        production = self.db.get_production(production_id, user_id)
        if not production:
            raise ProductionNotFound("创作任务不存在")
        if production["status"] == "running":
            raise ProductionConflict("任务正在运行")
        if production.get("status") != "awaiting_video_choice":
            raise ProductionConflict("当前任务不在视频选择阶段")
        chosen = next(
            (
                item
                for item in production.get("candidate_videos") or []
                if str(item.get("id") or "") == str(video_id or "")
            ),
            None,
        )
        if not chosen:
            raise ValueError("候选视频不存在")
        script = chosen.get("script") or {}
        clips = chosen.get("clips") or []
        output = chosen.get("output") or {}
        if not clips or not output.get("video_path"):
            raise ValueError("候选视频缺少可继续合成的片段或成片")
        self.db.update_production(
            production_id,
            {
                "script": script,
                "selected_script_id": chosen["id"],
                "clips": clips,
                "output": output,
                "error": None,
            },
        )
        self.sync_conversation_cards(production_id)
        return self.run(production_id, wait=wait, resume_from="finalize", user_id=user_id)

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
        self.db.update_production(production_id, {"status": "cancelled"})
        self.db.cancel_production_jobs(production_id)
        self.sync_conversation_cards(production_id)
        return self.public(production_id, user_id=user_id)

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
        raw = str(name or "").replace("\\", "/").strip()
        parts = Path(raw).parts
        if not raw or raw.startswith(("/", "\\")) or any(part == ".." for part in parts):
            raise ValueError("非法文件名")
        artifact_root = production_artifact_dir(
            self.settings.artifacts_dir,
            production.get("user_id"),
            production_id,
        )
        legacy_root = self.settings.artifacts_dir / "productions" / production_id
        if user_id and not (artifact_root / raw).is_file() and (legacy_root / raw).is_file():
            artifact_root = legacy_root
        path = (artifact_root / raw).resolve()
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
        intent: dict[str, Any] | None = None,
        skeleton: dict[str, Any] | None = None,
        script_candidates: list[dict[str, Any]] | None = None,
        prepare_only: bool = False,
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
        self.update_creative_context(
            production["id"],
            intent=intent,
            skeleton=skeleton,
            script_candidates=script_candidates,
            user_id=user_id,
        )
        if prepare_only:
            return self.prepare(production["id"], user_id=user_id)
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
        events = self.db.list_production_events(
            production_id,
            limit=180,
            newest_first=True,
        )
        events.reverse()
        cards = cards_for(
            public,
            artifact_dir if artifact_dir.is_dir() else None,
            events=events,
        )
        current_types = {item_type for item_type, _payload in cards}
        for item_type in VIDEO_CARD_TYPES:
            if item_type in current_types:
                continue
            item_id = stable_item_id(production_id, item_type)
            if self.db.delete_item(item_id, conversation_id):
                self.db.append_event(
                    conversation_id,
                    "item_deleted",
                    {
                        "item_id": item_id,
                        "item_type": item_type,
                        "production_id": production_id,
                    },
                )
        last_item = None
        for item_type, card_payload in cards:
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

    def _orchestrator(self):
        agent = getattr(self, "agent", None)
        if agent is None:
            raise RuntimeError("后台创作执行器尚未就绪，无法出片")
        return agent

    def recover_orphaned_runs(self, *, wait: bool = False) -> list[str]:
        """Called after agent wiring; resume saved plans, never re-ingest generated tasks."""
        recovered = []
        orphaned = self.db.recover_production_jobs()
        legacy_script_choice = self.db.legacy_script_choice_productions()
        legacy_ids = set(legacy_script_choice)
        recoveries = [
            (production_id, None)
            for production_id in orphaned
            if production_id not in legacy_ids
        ]
        recoveries.extend((production_id, "generate") for production_id in legacy_script_choice)
        for production_id, resume_from in recoveries:
            with self._lock:
                if production_id in self._cancels:
                    continue
                if resume_from == "generate":
                    self.db.update_production(
                        production_id,
                        {
                            "status": "running",
                            "current_stage": "generate",
                            "error": None,
                        },
                    )
                cancel = threading.Event()
                self._cancels[production_id] = cancel
                thread = threading.Thread(target=self._run_thread,
                                          args=(production_id, cancel, resume_from or "recover"),
                                          name=f"production-recover-{production_id}", daemon=True)
                self._threads[production_id] = thread
            recovered.append(production_id)
            if wait:
                self._run_thread(production_id, cancel, resume_from or "recover")
                with self._lock:
                    self._threads.pop(production_id, None)
            else:
                thread.start()
        return recovered

    def _run_thread(self, production_id: str, cancel: threading.Event, resume_from: str | None = None) -> None:
        thread = threading.current_thread()
        logger.info("production.run_thread_started resume_from=%s", resume_from or "-")
        try:
            self._orchestrator().run(production_id, cancel, resume_from=resume_from)
        except Exception:
            logger.exception("production.run_thread_failed")
            pass
        finally:
            with self._lock:
                if self._cancels.get(production_id) is cancel:
                    self._cancels.pop(production_id, None)
                if self._threads.get(production_id) is thread:
                    self._threads.pop(production_id, None)
            logger.info("production.run_thread_finished")

    def _to_public(self, production: dict[str, Any]) -> dict[str, Any]:
        production_id = production["id"]
        context_paths = creative_context_paths(self.settings, production)
        output = production.get("output") or None
        if isinstance(output, dict):
            output = {
                **{
                    key: value
                    for key, value in output.items()
                    if key not in {"srt_path", "srt_url"}
                },
                "video_url": output.get("video_url") or f"/api/productions/{production_id}/artifacts/final.mp4",
            }
        candidate_videos = []
        raw_candidate_videos = list(production.get("candidate_videos") or [])
        visible_candidate_videos = raw_candidate_videos if len(raw_candidate_videos) == 3 else []
        for item in visible_candidate_videos:
            if not isinstance(item, dict):
                continue
            candidate_output = item.get("output") if isinstance(item.get("output"), dict) else {}
            video_name = Path(str(candidate_output.get("video_path") or item.get("video_path") or "")).name
            candidate_videos.append(
                {
                    "id": item.get("id"),
                    "script_id": item.get("script_id") or item.get("id"),
                    "title": item.get("title") or item.get("id"),
                    "angle": item.get("angle") or "",
                    "status": item.get("status") or "ready",
                    "video_url": (
                        candidate_output.get("video_url")
                        or item.get("video_url")
                        or (f"/api/productions/{production_id}/artifacts/{video_name}" if video_name else "")
                    ),
                    "duration_ms": int(item.get("duration_ms") or 0),
                    "width": int(item.get("width") or 0),
                    "height": int(item.get("height") or 0),
                }
            )
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
            "replace_policy": production.get("replace_policy")
            or {
                "mode": "default",
                "roles": [],
                "segment_ids": [],
                "time_ranges": [],
            },
            "replace_plan": production.get("replace_plan"),
            "reference_images": production.get("reference_images") or [],
            "conversation_id": production.get("conversation_id"),
            "landing_page": production.get("landing_page"),
            "script_candidates": production.get("script_candidates") or [],
            "selected_script_id": production.get("selected_script_id"),
            "candidate_videos": candidate_videos,
            "creative_context": context_paths,
            "agent_trace": production.get("agent_trace") or [],
            "created_at": production["created_at"],
            "updated_at": production["updated_at"],
        }
