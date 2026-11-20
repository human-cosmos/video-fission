from __future__ import annotations

import logging
import random
import shutil
import threading
import uuid
from contextvars import copy_context
from pathlib import Path
from typing import Any

from . import media
from .config import Settings
from .db import Database
from .jobs import JobRunner, digest
from .paths import production_artifact_dir
from .pipeline import PipelineEngine
from .productions import ProductionConflict, ProductionNotFound
from .settings_store import SettingsStore
from .understand.service import run_decompose

logger = logging.getLogger(__name__)

REMIX_TASK_KIND = "reference_remix"
REMIX_ID_PREFIX = "rmx_"
DEFAULT_STRATEGY = "seeded_shuffle"
SUPPORTED_STRATEGIES = {"seeded_shuffle", "reverse", "manual"}
REORDERABLE_ROLES = {"value"}
MAX_AUTO_REORDERED_UNITS = 3


class ReferenceRemixService:
    """Reference-only video remix: reorder original segments without video generation."""

    def __init__(
        self,
        settings: Settings,
        db: Database,
        store: SettingsStore,
        engine: PipelineEngine,
    ):
        self.settings = settings
        self.db = db
        self.store = store
        self.engine = engine
        self._lock = threading.Lock()
        self._cancels: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}

    def create(
        self,
        video_upload_id: str,
        *,
        title: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_enabled()
        upload = self.db.get_video_upload(video_upload_id, user_id)
        if not upload:
            raise ValueError("参考视频不存在")
        remix_id = f"{REMIX_ID_PREFIX}{uuid.uuid4().hex[:24]}"
        title_text = (title or Path(str(upload.get("name") or "原片重排")).stem).strip()[:120]
        source_video = {
            "upload_id": upload["id"],
            "filename": upload["name"],
            "path": upload["path"],
            "duration_ms": upload["duration_ms"],
            "width": upload["width"],
            "height": upload["height"],
            "fps": upload["fps"],
            "has_audio": bool(media.probe(Path(upload["path"])).get("has_audio")),
        }
        with self._lock:
            existing = self.db.find_production_by_upload(
                video_upload_id,
                task_kind=REMIX_TASK_KIND,
                user_id=user_id,
            )
            if existing and existing.get("status") in {
                "running",
                "awaiting_order",
                "completed",
            }:
                return self.public(existing["id"], user_id=user_id)
            self.db.create_production(
                {
                    "id": remix_id,
                    "user_id": user_id,
                    "task_kind": REMIX_TASK_KIND,
                    "title": title_text or "原片重排",
                    "brief": "",
                    "status": "running",
                    "current_stage": "decompose",
                    "warnings": [],
                    "source_video": source_video,
                    "decomposition": None,
                    "clips": [],
                    "output": None,
                    "remix_plan": {
                        "phase": "queued",
                        "strategy": DEFAULT_STRATEGY,
                        "seed": remix_id,
                    },
                    "video_upload_id": upload["id"],
                    "conversation_id": None,
                }
            )
        self._start_analysis(remix_id)
        return self.public(remix_id, user_id=user_id)

    def get(self, remix_id: str, user_id: str | None = None) -> dict[str, Any]:
        return self._record(remix_id, user_id)

    def public(self, remix_id: str, user_id: str | None = None) -> dict[str, Any]:
        production = self._record(remix_id, user_id)
        artifact_dir = self.artifact_dir(remix_id, user_id=user_id)
        decomposition = production.get("decomposition") or {}
        segments = []
        for segment in decomposition.get("segments") or []:
            item = dict(segment)
            clip_name = str(item.get("clip_path") or "")
            if clip_name and (artifact_dir / clip_name).is_file():
                item["clip_url"] = self._artifact_url(remix_id, clip_name)
            frames = []
            for index in range(1, 4):
                frame_name = f"{item.get('id')}_frame_{index:02d}.jpg"
                if (artifact_dir / frame_name).is_file():
                    frames.append(self._artifact_url(remix_id, frame_name))
            item["frame_urls"] = frames
            item["width"] = int((production.get("source_video") or {}).get("width") or 0)
            item["height"] = int((production.get("source_video") or {}).get("height") or 0)
            segments.append(item)

        output = production.get("output") or None
        if isinstance(output, dict):
            output = {
                "video_url": output.get("video_url") or "",
                "duration_ms": int(output.get("duration_ms") or 0),
                "width": int(output.get("width") or 0),
                "height": int(output.get("height") or 0),
                "filename": Path(str(output.get("video_path") or "")).name,
            }
        return {
            "id": production["id"],
            "task_kind": REMIX_TASK_KIND,
            "title": production.get("title") or "原片重排",
            "status": production.get("status"),
            "current_stage": production.get("current_stage"),
            "error": production.get("error"),
            "warnings": production.get("warnings") or [],
            "source_video": production.get("source_video") or {},
            "summary": decomposition.get("summary") or "",
            "provider": decomposition.get("provider") or "",
            "model": decomposition.get("model") or "",
            "opening": decomposition.get("opening") or {},
            "segments": segments,
            "remix_plan": production.get("remix_plan") or {},
            "output": output,
            "created_at": production.get("created_at"),
            "updated_at": production.get("updated_at"),
        }

    def render(
        self,
        remix_id: str,
        *,
        opening_end_ms: int | None = None,
        ending_start_ms: int | None = None,
        middle_order: list[str] | None = None,
        strategy: str | None = None,
        seed: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_enabled()
        production = self._record(remix_id, user_id)
        if production.get("status") == "running":
            raise ProductionConflict("原片重排任务正在运行")
        with self._lock:
            active_thread = self._threads.get(remix_id)
        if active_thread and active_thread.is_alive():
            raise ProductionConflict("原片重排任务正在停止，请稍后再试")
        if not production.get("decomposition"):
            raise ProductionConflict("原片拆解尚未完成")
        plan = self._build_plan(
            production,
            opening_end_ms=opening_end_ms,
            ending_start_ms=ending_start_ms,
            middle_order=middle_order,
            strategy=strategy,
            seed=seed,
        )
        plan["phase"] = "render_queued"
        self.db.update_production(
            remix_id,
            {
                "status": "running",
                "current_stage": "render",
                "error": None,
                "output": None,
                "remix_plan": plan,
            },
        )
        self._emit(remix_id, "remix_plan_ready", {"plan": _public_plan(plan)})
        self._start_render(remix_id)
        return self.public(remix_id, user_id=user_id)

    def cancel(self, remix_id: str, user_id: str | None = None) -> dict[str, Any]:
        production = self._record(remix_id, user_id)
        if production.get("status") in {"completed", "failed"}:
            raise ProductionConflict("已结束的任务不能取消")
        with self._lock:
            cancel = self._cancels.get(remix_id)
        if cancel:
            cancel.set()
        self.db.update_production(remix_id, {"status": "cancelled"})
        self.db.cancel_production_jobs(remix_id)
        self._emit(remix_id, "cancelled", {"message": "原片重排已取消"})
        return self.public(remix_id, user_id=user_id)

    def delete(self, remix_id: str, user_id: str | None = None) -> None:
        production = self._record(remix_id, user_id)
        if production.get("status") == "running":
            raise ProductionConflict("运行中的原片重排请先取消")
        self.db.delete_production(remix_id)
        shutil.rmtree(self.artifact_dir(remix_id, user_id=user_id), ignore_errors=True)

    def events(
        self,
        remix_id: str,
        *,
        after_id: int = 0,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        self._record(remix_id, user_id)
        return self.db.list_production_events(
            remix_id,
            after_id=after_id,
            user_id=user_id,
        )

    def artifact_path(
        self,
        remix_id: str,
        name: str,
        user_id: str | None = None,
    ) -> Path:
        self._record(remix_id, user_id)
        raw = str(name or "").replace("\\", "/").strip()
        parts = Path(raw).parts
        if not raw or raw.startswith(("/", "\\")) or any(part == ".." for part in parts):
            raise ValueError("非法文件名")
        root = self.artifact_dir(remix_id, user_id=user_id).resolve()
        path = (root / raw).resolve()
        if root not in path.parents and path != root:
            raise ValueError("非法文件名")
        if not path.is_file():
            raise FileNotFoundError("产物不存在")
        return path

    def artifact_dir(self, remix_id: str, user_id: str | None = None) -> Path:
        production = self.db.get_production(
            remix_id,
            user_id,
            task_kind=REMIX_TASK_KIND,
        )
        if not production:
            return production_artifact_dir(self.settings.artifacts_dir, user_id, remix_id)
        path = production_artifact_dir(
            self.settings.artifacts_dir,
            production.get("user_id"),
            remix_id,
        )
        path.mkdir(parents=True, exist_ok=True)
        return path

    def recover_orphaned_runs(self, *, wait: bool = False) -> list[str]:
        recovered: list[str] = []
        for production in self.db.list_productions_by_status(
            REMIX_TASK_KIND,
            ("running", "awaiting_order"),
        ):
            remix_id = production["id"]
            with self._lock:
                if remix_id in self._cancels:
                    continue
            plan = production.get("remix_plan") or {}
            if production.get("decomposition") and plan.get("ordered_units"):
                if production.get("status") == "awaiting_order":
                    try:
                        rebuilt_plan = self._build_plan(production)
                    except Exception as exc:  # noqa: BLE001
                        logger.exception(
                            "reference_remix.recovery_plan_failed remix=%s",
                            remix_id,
                        )
                        self._fail(remix_id, f"原片重排恢复失败：{exc}")
                        continue
                    self._queue_default_render(remix_id, rebuilt_plan, wait=wait)
                else:
                    self._start_render(remix_id, wait=wait)
            else:
                self._start_analysis(remix_id, wait=wait)
            recovered.append(remix_id)
        return recovered

    def _queue_default_render(
        self,
        remix_id: str,
        plan: dict[str, Any],
        *,
        wait: bool = False,
        cancel: threading.Event | None = None,
    ) -> None:
        queued_plan = {**plan, "phase": "render_queued"}
        self.db.update_production(
            remix_id,
            {
                "remix_plan": queued_plan,
                "status": "running",
                "current_stage": "render",
                "error": None,
                "output": None,
            },
        )
        self._emit(
            remix_id,
            "remix_plan_ready",
            {"plan": _public_plan(queued_plan)},
        )
        self._start_render(remix_id, wait=wait, cancel=cancel)

    def _start_analysis(self, remix_id: str, *, wait: bool = False) -> None:
        production = self._record(remix_id)
        self.db.update_production(
            remix_id,
            {
                "status": "running",
                "current_stage": "decompose",
                "error": None,
                "warnings": list(production.get("warnings") or []),
            },
        )
        cancel = threading.Event()
        with self._lock:
            self._cancels[remix_id] = cancel
        context = copy_context()

        def target() -> None:
            context.run(self._run_analysis, remix_id, cancel)

        thread = threading.Thread(
            target=target,
            name=f"remix-analysis-{remix_id}",
            daemon=True,
        )
        with self._lock:
            self._threads[remix_id] = thread
        if wait:
            self._run_analysis(remix_id, cancel)
            with self._lock:
                if self._threads.get(remix_id) is thread:
                    self._threads.pop(remix_id, None)
                if self._cancels.get(remix_id) is cancel:
                    self._cancels.pop(remix_id, None)
        else:
            thread.start()

    def _run_analysis(self, remix_id: str, cancel: threading.Event) -> None:
        thread = threading.current_thread()
        try:
            production = self._record(remix_id)
            artifact_dir = self.artifact_dir(remix_id, user_id=production.get("user_id"))
            source = Path(str((production.get("source_video") or {}).get("path") or ""))
            if not source.is_file():
                raise ValueError("参考视频不存在")
            dest = artifact_dir / f"source{source.suffix or '.mp4'}"
            if source.resolve() != dest.resolve():
                shutil.copyfile(source, dest)
            info = media.probe(dest)
            source_video = {
                **(production.get("source_video") or {}),
                "path": str(dest),
                "duration_ms": info["duration_ms"],
                "width": info["width"],
                "height": info["height"],
                "fps": info["fps"],
                "has_audio": bool(info.get("has_audio")),
            }
            self.db.update_production(remix_id, {"source_video": source_video})
            public_settings, secrets = self.engine._settings_for_production(remix_id)
            warnings = list(production.get("warnings") or [])
            existing_decomposition = production.get("decomposition") or {}
            if existing_decomposition.get("segments"):
                decomposition = existing_decomposition
                self._emit(
                    remix_id,
                    "stage_completed",
                    {
                        "stage": "decompose",
                        "step": "understand",
                        "message": "复用已完成的原片拆解",
                    },
                )
            else:
                self._emit(remix_id, "stage_started", {"stage": "decompose", "step": "understand"})
                decomposition = run_decompose(
                    {**production, "source_video": source_video},
                    artifact_dir,
                    public_settings,
                    secrets,
                    warnings,
                    emit=lambda kind, payload: self._emit(remix_id, kind, payload),
                    cancel=cancel,
                    allow_local_fallback=True,
                )
            self._check_cancelled(remix_id, cancel)
            updated = {**production, "source_video": source_video, "decomposition": decomposition}
            self.db.update_production(
                remix_id,
                {
                    "source_video": source_video,
                    "decomposition": decomposition,
                    "warnings": warnings,
                },
            )
            plan = self._build_plan(updated)
            plan["phase"] = "render_queued"
            for warning in plan.get("warnings") or []:
                if warning not in warnings:
                    warnings.append(warning)
            self.db.update_production(
                remix_id,
                {
                    "decomposition": decomposition,
                    "warnings": warnings,
                },
            )
            self._queue_default_render(remix_id, plan, cancel=cancel)
        except Exception as exc:  # noqa: BLE001
            if cancel.is_set():
                self.db.update_production(remix_id, {"status": "cancelled"})
            else:
                logger.exception("reference_remix.analysis_failed remix=%s", remix_id)
                self._fail(remix_id, f"原片拆解失败：{exc}")
        finally:
            self._cleanup_thread(remix_id, thread)

    def _start_render(
        self,
        remix_id: str,
        *,
        wait: bool = False,
        cancel: threading.Event | None = None,
    ) -> None:
        cancel = cancel or threading.Event()
        with self._lock:
            self._cancels[remix_id] = cancel
        context = copy_context()

        def target() -> None:
            context.run(self._run_render, remix_id, cancel)

        thread = threading.Thread(
            target=target,
            name=f"remix-render-{remix_id}",
            daemon=True,
        )
        with self._lock:
            self._threads[remix_id] = thread
        if wait:
            self._run_render(remix_id, cancel)
            with self._lock:
                if self._threads.get(remix_id) is thread:
                    self._threads.pop(remix_id, None)
                if self._cancels.get(remix_id) is cancel:
                    self._cancels.pop(remix_id, None)
        else:
            thread.start()

    def _run_render(self, remix_id: str, cancel: threading.Event) -> None:
        thread = threading.current_thread()
        try:
            production = self._record(remix_id)
            plan = production.get("remix_plan") or {}
            if not plan.get("ordered_units"):
                raise ValueError("重排计划不存在")
            source_info = production.get("source_video") or {}
            source = Path(str(source_info.get("path") or ""))
            if not source.is_file():
                raise ValueError("参考视频不存在")
            artifact_dir = self.artifact_dir(remix_id, user_id=production.get("user_id"))
            source_stat = source.stat()
            source_fingerprint = {
                "size": source_stat.st_size,
                "mtime_ns": source_stat.st_mtime_ns,
            }
            target_width, target_height = media.target_dimensions(
                int(source_info.get("width") or 0),
                int(source_info.get("height") or 0),
            )
            plan_digest = digest({"plan": plan, "source": source_fingerprint})[:16]
            runner = JobRunner(self.db, remix_id, cancel)
            self._emit(
                remix_id,
                "stage_started",
                {"stage": "render", "step": "units", "message": "开始按新顺序拼接原片片段"},
            )

            def operation(_job: dict[str, Any]) -> dict[str, Any]:
                clips: list[dict[str, Any]] = []
                total = len(plan["ordered_units"])
                for index, unit in enumerate(plan["ordered_units"], start=1):
                    runner.check_cancelled()
                    duration_ms = int(unit["end_ms"]) - int(unit["start_ms"])
                    if duration_ms <= 0:
                        raise ValueError(f"片段 {unit.get('id')} 时长无效")
                    unit_dir = artifact_dir / "remix"
                    unit_dir.mkdir(parents=True, exist_ok=True)
                    dest = unit_dir / f"{index:02d}-{unit['id']}.mp4"
                    media.cut_segment(
                        source,
                        dest,
                        int(unit["start_ms"]),
                        int(unit["end_ms"]),
                        target_width,
                        target_height,
                        float(source_info.get("fps") or 30),
                        bool(source_info.get("has_audio")),
                    )
                    info = media.probe(dest)
                    if not info.get("has_audio"):
                        silent = dest.with_name(dest.stem + "_silent.mp4")
                        media.add_silence_audio(dest, silent, duration_ms)
                        silent.replace(dest)
                        info = media.probe(dest)
                    clips.append(
                        {
                            "shot_id": unit["id"],
                            "source_kind": unit["kind"],
                            "source_segment_id": unit.get("source_segment_id"),
                            "path": str(dest),
                            "url": self._artifact_url(remix_id, f"remix/{dest.name}"),
                            "provider": "reference_reuse",
                            "duration_ms": info["duration_ms"],
                        }
                    )
                    self._emit(
                        remix_id,
                        "progress",
                        {
                            "stage": "render",
                            "current": index,
                            "total": total,
                            "message": f"正在拼接 {index}/{total}",
                        },
                    )
                runner.check_cancelled()
                concat_path = artifact_dir / f"remix-concat-{plan_digest}.mp4"
                media.concat_clips([Path(item["path"]) for item in clips], concat_path)
                final_path = artifact_dir / f"remix-{plan_digest}.mp4"
                media.fit_av_duration(
                    concat_path,
                    final_path,
                    int(plan["total_duration_ms"]),
                    float(source_info.get("fps") or 30),
                )
                media.validate_video_decode(final_path)
                info = media.probe(final_path)
                if abs(int(info["duration_ms"]) - int(plan["total_duration_ms"])) > 500:
                    raise ValueError("成片时长与重排计划偏差超过 500ms")
                return {
                    "video_path": str(final_path),
                    "video_url": self._artifact_url(remix_id, final_path.name),
                    "duration_ms": int(info["duration_ms"]),
                    "width": int(info["width"]),
                    "height": int(info["height"]),
                    "clips": clips,
                }

            result = runner.run(
                "render_reference_remix",
                {
                    "plan": plan,
                    "source": str(source),
                    "source_fingerprint": source_fingerprint,
                },
                operation,
            )
            runner.check_cancelled()
            self.db.update_production(
                remix_id,
                {
                    "clips": result.get("clips") or [],
                    "output": {
                        "video_path": result["video_path"],
                        "video_url": result["video_url"],
                        "duration_ms": result["duration_ms"],
                        "width": result["width"],
                        "height": result["height"],
                    },
                    "remix_plan": {
                        **plan,
                        "phase": "completed",
                    },
                    "status": "completed",
                    "current_stage": "done",
                    "error": None,
                },
            )
            self._emit(
                remix_id,
                "stage_completed",
                {"stage": "render", "step": "units", "message": "原片重排拼接完成"},
            )
            self._emit(
                remix_id,
                "completed",
                {"stage": "done", "video_url": result["video_url"]},
            )
        except Exception as exc:  # noqa: BLE001
            if cancel.is_set():
                self.db.update_production(remix_id, {"status": "cancelled"})
            else:
                logger.exception("reference_remix.render_failed remix=%s", remix_id)
                self._fail(remix_id, f"原片重排失败：{exc}")
        finally:
            self._cleanup_thread(remix_id, thread)

    def _build_plan(
        self,
        production: dict[str, Any],
        *,
        opening_end_ms: int | None = None,
        ending_start_ms: int | None = None,
        middle_order: list[str] | None = None,
        strategy: str | None = None,
        seed: str | None = None,
    ) -> dict[str, Any]:
        decomposition = production.get("decomposition") or {}
        segments = sorted(
            (
                dict(item)
                for item in decomposition.get("segments") or []
                if int(item.get("end_ms") or 0) > int(item.get("start_ms") or 0)
            ),
            key=lambda item: (int(item.get("start_ms") or 0), int(item.get("end_ms") or 0)),
        )
        if len(segments) < 3:
            raise ValueError("原片片段不足，无法保留开头和结尾后重排中间片段")
        source = production.get("source_video") or {}
        duration_ms = int(source.get("duration_ms") or segments[-1]["end_ms"])
        opening = decomposition.get("opening") or {}
        opening_end = int(opening_end_ms or opening.get("end_ms") or segments[0]["end_ms"])
        ending_start = int(
            ending_start_ms
            if ending_start_ms is not None
            else self._infer_ending_start(segments)
        )
        opening_end = self._snap_boundary(opening_end, segments, "end")
        ending_start = self._snap_boundary(ending_start, segments, "start")
        plan_warnings: list[str] = []
        if (
            opening_end_ms is None
            and ending_start - opening_end < 500
            and str(opening.get("method") or "") == "local_fallback"
        ):
            first_segment_end = int(segments[0]["end_ms"])
            fallback_end = (
                first_segment_end
                if first_segment_end <= ending_start - 500
                else min(int(duration_ms * 0.15), ending_start - 500)
            )
            if fallback_end >= 500:
                opening_end = self._snap_boundary(fallback_end, segments, "end")
                plan_warnings.append(
                    "本地兜底未给出可用开头范围，已收敛开头区间并为中间重排保留空间"
                )
        if opening_end < 500:
            raise ValueError("开头保留区间过短")
        if ending_start > duration_ms - 500:
            raise ValueError("结尾保留区间过短")
        if ending_start - opening_end < 500:
            raise ValueError("开头和结尾之间没有足够的可重排区间")

        middle_units = self._middle_units(segments, opening_end, ending_start)
        if len(middle_units) < 2:
            raise ValueError("中间片段不足两个，无法生成重排方案")
        reorderable = [item for item in middle_units if item.get("role") in REORDERABLE_ROLES]
        if len(reorderable) < 2:
            raise ValueError("中间 value 片段不足两个，无法生成重排方案")

        selected_strategy = str(
            strategy
            or (production.get("remix_plan") or {}).get("strategy")
            or DEFAULT_STRATEGY
        ).strip()
        if selected_strategy not in SUPPORTED_STRATEGIES:
            raise ValueError("不支持的重排策略")
        selected_seed = str(
            seed
            or (production.get("remix_plan") or {}).get("seed")
            or production.get("id")
            or uuid.uuid4().hex
        )
        if (
            selected_strategy == "manual"
            and middle_order is None
            and (production.get("remix_plan") or {}).get("middle_order")
        ):
            middle_order = list((production.get("remix_plan") or {}).get("middle_order") or [])
        if selected_strategy == "manual" and not middle_order:
            raise ValueError("手动重排缺少中间片段顺序")
        ordered_ids = self._ordered_middle_ids(
            middle_units,
            reorderable,
            strategy=selected_strategy,
            seed=selected_seed,
            middle_order=middle_order,
        )
        adjusted_value_ids = self._changed_value_ids(
            [item["id"] for item in middle_units],
            ordered_ids,
            {item["id"] for item in reorderable},
        )
        if (
            selected_strategy != "manual"
            and len(adjusted_value_ids) > MAX_AUTO_REORDERED_UNITS
        ):
            raise ValueError(
                f"自动重排最多调整 {MAX_AUTO_REORDERED_UNITS} 个 value 片段"
            )
        reorderable_id_set = {item["id"] for item in reorderable}
        for index, unit in enumerate(middle_units):
            if unit["id"] not in reorderable_id_set and ordered_ids[index] != unit["id"]:
                raise ValueError("手动顺序不能移动非 value 锚点片段")
        by_id = {item["id"]: item for item in middle_units}
        ordered_middle = [by_id[item_id] for item_id in ordered_ids]
        opening_unit = {
            "id": "remix_opening",
            "kind": "opening",
            "role": "hook",
            "source_segment_id": (opening.get("segment_ids") or [None])[0],
            "start_ms": 0,
            "end_ms": opening_end,
        }
        ending_unit = {
            "id": "remix_ending",
            "kind": "ending",
            "role": "cta",
            "source_segment_id": None,
            "start_ms": ending_start,
            "end_ms": duration_ms,
        }
        ordered_units = [opening_unit, *ordered_middle, ending_unit]
        total_duration_ms = sum(int(item["end_ms"]) - int(item["start_ms"]) for item in ordered_units)
        if abs(total_duration_ms - duration_ms) > 100:
            raise ValueError(
                f"重排片段未完整覆盖原片：计划 {total_duration_ms}ms，源视频 {duration_ms}ms"
            )
        return {
            "phase": "planned",
            "strategy": selected_strategy,
            "seed": selected_seed,
            "opening": {"start_ms": 0, "end_ms": opening_end},
            "ending": {"start_ms": ending_start, "end_ms": duration_ms},
            "middle_units": middle_units,
            "ordered_units": ordered_units,
            "middle_order": ordered_ids,
            "total_duration_ms": total_duration_ms,
            "reorderable_ids": [item["id"] for item in reorderable],
            "adjusted_value_ids": adjusted_value_ids,
            "adjusted_value_count": len(adjusted_value_ids),
            "max_auto_adjusted_units": MAX_AUTO_REORDERED_UNITS,
            "video_model_called": False,
            "warnings": plan_warnings,
        }

    @staticmethod
    def _middle_units(
        segments: list[dict[str, Any]],
        opening_end: int,
        ending_start: int,
    ) -> list[dict[str, Any]]:
        units: list[dict[str, Any]] = []
        for segment in segments:
            start = max(int(segment["start_ms"]), opening_end)
            end = min(int(segment["end_ms"]), ending_start)
            if end <= start:
                continue
            units.append(
                {
                    "id": str(segment["id"]),
                    "kind": "middle",
                    "role": str(segment.get("role") or "other"),
                    "source_segment_id": str(segment["id"]),
                    "start_ms": start,
                    "end_ms": end,
                    "summary": str(segment.get("summary") or segment.get("description") or ""),
                }
            )
        return units

    @staticmethod
    def _ordered_middle_ids(
        middle_units: list[dict[str, Any]],
        reorderable: list[dict[str, Any]],
        *,
        strategy: str,
        seed: str,
        middle_order: list[str] | None,
    ) -> list[str]:
        all_ids = [item["id"] for item in middle_units]
        if middle_order:
            requested = [str(item).strip() for item in middle_order if str(item).strip()]
            if len(requested) != len(set(requested)) or set(requested) != set(all_ids):
                raise ValueError("中间片段顺序必须完整包含所有片段且不能重复")
            return requested

        original = [item["id"] for item in middle_units]
        value_ids = [item["id"] for item in reorderable]
        value_id_set = set(value_ids)
        if strategy == "reverse":
            ordered_values = list(reversed(value_ids))
        else:
            ordered_values = list(value_ids)
            if len(ordered_values) > 1:
                pair_index = random.Random(seed).randrange(len(ordered_values) - 1)
                ordered_values[pair_index], ordered_values[pair_index + 1] = (
                    ordered_values[pair_index + 1],
                    ordered_values[pair_index],
                )
        value_iter = iter(ordered_values)
        return [
            next(value_iter) if item_id in value_id_set else item_id
            for item_id in original
        ]

    @staticmethod
    def _changed_value_ids(
        original_ids: list[str],
        ordered_ids: list[str],
        value_id_set: set[str],
    ) -> list[str]:
        return [
            original_id
            for original_id, ordered_id in zip(original_ids, ordered_ids)
            if original_id in value_id_set and original_id != ordered_id
        ]

    @staticmethod
    def _infer_ending_start(segments: list[dict[str, Any]]) -> int:
        index = len(segments) - 1
        while index > 0 and str(segments[index].get("role") or "") == "cta":
            index -= 1
        if index < len(segments) - 1:
            return int(segments[index + 1]["start_ms"])
        return int(segments[-1]["start_ms"])

    @staticmethod
    def _snap_boundary(value: int, segments: list[dict[str, Any]], kind: str) -> int:
        if kind == "end":
            points = [int(segment["end_ms"]) for segment in segments]
        else:
            points = [int(segment["start_ms"]) for segment in segments]
        if not points:
            return int(value)
        nearest = min(points, key=lambda point: abs(point - int(value)))
        if abs(nearest - int(value)) <= 600:
            return int(nearest)
        return int(value)

    def _record(self, remix_id: str, user_id: str | None = None) -> dict[str, Any]:
        production = self.db.get_production(
            remix_id,
            user_id,
            task_kind=REMIX_TASK_KIND,
        )
        if not production:
            raise ProductionNotFound("原片重排任务不存在")
        return production

    def _require_enabled(self) -> None:
        if not self.settings.enable_reference_remix:
            raise ProductionNotFound("原片重排功能未启用")

    def _check_cancelled(self, remix_id: str, cancel: threading.Event) -> None:
        production = self.db.get_production(remix_id, task_kind=REMIX_TASK_KIND) or {}
        if cancel.is_set() or production.get("status") == "cancelled":
            cancel.set()
            self.db.cancel_production_jobs(remix_id)
            raise RuntimeError("原片重排已取消")

    def _fail(self, remix_id: str, message: str) -> None:
        self.db.update_production(
            remix_id,
            {"status": "failed", "error": message},
        )
        self._emit(remix_id, "error", {"message": message})

    def _cleanup_thread(self, remix_id: str, thread: threading.Thread) -> None:
        with self._lock:
            if self._threads.get(remix_id) is thread:
                self._threads.pop(remix_id, None)
                self._cancels.pop(remix_id, None)

    def _emit(self, remix_id: str, kind: str, payload: dict[str, Any]) -> None:
        self.engine._emit(remix_id, kind, payload)

    def _artifact_url(self, remix_id: str, name: str) -> str:
        return f"/api/remixes/{remix_id}/artifacts/{name}"


def _public_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in plan.items()
        if key not in {"middle_units"}
    }
