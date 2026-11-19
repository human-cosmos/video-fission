"""Fine-grained production tools for the deterministic backend runner."""

from __future__ import annotations

import logging
import re
import shutil
import threading
from pathlib import Path
from typing import Any

from . import media
from .audio_separate import separate_reference_audio
from .jobs import JobRunner, digest, revision
from .pipeline import REMOTE_VIDEO_PROVIDERS, PipelineEngine, local_script
from .production_context import creative_context_paths, write_creative_context
from .references import ReferenceLibrary
from .script_flow import (
    classify_followup,
    lock_script_to_timeline,
    parse_script_selection,
)
from .subtitle_style import detect_subtitle_style
from .tools import ToolDefinition, ToolRegistry

logger = logging.getLogger(__name__)

FORBIDDEN_CLAIMS = ("已还原参考", "已按参考对齐", "已还原")
_VAGUE_FOLLOWUP_RE = re.compile(r"再改一下|再改改|不满意|不太满意|重新改(?!脚本)")

CAPABILITY_TOOLS = (
    "ingest_reference_video",
    "understand_reference_video",
    "extract_landing_points",
    "search_video_references",
    "lock_timeline_opening",
    "write_intent_and_scripts",
    "get_production_state",
    "update_production_progress",
    "generate_candidate_videos",
    "generate_video_shots",
    "separate_audio",
    "burn_subtitles",
    "compose_timeline",
    "handle_production_followup",
)


class ProductionToolError(ValueError):
    pass


def contains_forbidden_claim(text: str) -> bool:
    blob = str(text or "")
    return any(phrase in blob for phrase in FORBIDDEN_CLAIMS)


def assert_no_forbidden_claim(payload: Any) -> None:
    if contains_forbidden_claim(_flatten(payload)):
        raise ProductionToolError("结果含有禁止的还原表述，已拒绝写入")


def _flatten(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_flatten(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_flatten(item) for item in value)
    return str(value or "")


def _srt_ts(ms: int) -> str:
    ms = max(int(ms), 0)
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def write_shot_srt(path: Path, shots: list[dict[str, Any]]) -> None:
    cursor = 0
    blocks: list[str] = []
    index = 1
    for shot in shots:
        duration = int(shot.get("duration_ms") or 0)
        text = str(shot.get("voiceover") or shot.get("on_screen_text") or "").strip()
        if text:
            blocks.append(f"{index}\n{_srt_ts(cursor)} --> {_srt_ts(cursor + duration)}\n{text}\n")
            index += 1
        cursor += duration
    path.write_text("\n".join(blocks) + ("\n" if blocks else ""), encoding="utf-8")


def assert_opening_locked(timeline: dict[str, Any], production: dict[str, Any]) -> None:
    opening = dict((timeline or {}).get("opening") or ((production.get("decomposition") or {}).get("opening") or {}))
    end_ms = int(opening.get("end_ms") or 0)
    if end_ms <= 0:
        return
    shots = list((timeline or {}).get("shots") or [])
    opening_shots = [
        shot
        for shot in shots
        if str(shot.get("id") or "") == "shot_opening" or shot.get("source_kind") == "opening"
    ]
    if len(opening_shots) != 1:
        raise ProductionToolError("开场必须锁定为单个连续的 shot_opening 块")
    block = opening_shots[0]
    if int(block.get("duration_ms") or 0) != end_ms:
        raise ProductionToolError("shot_opening 时长必须覆盖 [0, opening_end)")
    cursor = 0
    for shot in shots:
        start = int(shot.get("start_ms") or cursor)
        duration = int(shot.get("duration_ms") or 0)
        if shot is not block and start < end_ms:
            raise ProductionToolError("不得在开头连续块内再切分镜头")
        cursor = start + duration


class ProductionToolHost:
    def __init__(self, engine: PipelineEngine, library: ReferenceLibrary | None = None):
        self.engine = engine
        self.db = engine.db
        self.settings = engine.settings
        self.store = engine.store
        self.library = library or getattr(engine, "library", None) or ReferenceLibrary(self.settings.project_root)

    def _production(self, production_id: str) -> dict[str, Any]:
        production = self.db.get_production(production_id)
        if not production:
            raise ProductionToolError("创作任务不存在")
        return production

    def _pid(self, arguments: dict[str, Any], context: dict[str, Any]) -> str:
        production_id = str(arguments.get("production_id") or context.get("production_id") or "").strip()
        if not production_id:
            raise ProductionToolError("缺少 production_id")
        return production_id

    def _cancel(self, context: dict[str, Any]) -> threading.Event:
        cancel = context.get("cancel")
        return cancel if isinstance(cancel, threading.Event) else threading.Event()

    def _warnings(self, production: dict[str, Any]) -> list[str]:
        return list(production.get("warnings") or [])

    def ingest_reference_video(self, arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        production_id = self._pid(arguments, context)
        warnings = self._warnings(self._production(production_id))
        self.engine._stage_ingest(production_id, self._cancel(context), warnings)
        production = self._production(production_id)
        source = production.get("source_video") or {}
        return {
            "production_id": production_id,
            "source_video": {
                "path": source.get("path"),
                "duration_ms": source.get("duration_ms"),
                "width": source.get("width"),
                "height": source.get("height"),
                "fps": source.get("fps"),
            },
        }

    def understand_reference_video(self, arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        from .understand.service import UnderstandError, run_decompose

        production_id = self._pid(arguments, context)
        warnings = self._warnings(self._production(production_id))
        try:
            self.engine._stage_decompose(production_id, self._cancel(context), warnings)
        except UnderstandError as exc:
            # Missing or unusable understanding model: honest local cut, never a fake model result.
            production = self._production(production_id)
            public, secrets = self.engine._settings_for_production(production_id)
            warnings.append(f"未配置或无法调用视频理解模型，已使用本地切分：{exc}")
            decomposition = run_decompose(
                production,
                self.engine.artifact_dir(production_id),
                public,
                secrets,
                warnings,
                emit=lambda kind, payload: self.engine._emit(production_id, kind, payload),
                cancel=self._cancel(context),
                allow_local_fallback=True,
            )
            self.db.update_production(production_id, {"decomposition": decomposition, "warnings": warnings})
        production = self._production(production_id)
        decomposition = production.get("decomposition") or {}
        method = str(decomposition.get("provider") or decomposition.get("method") or "local")
        used_model = method not in {"", "local", "local_ffmpeg", "local_fallback", "ffmpeg"}
        result = {
            "production_id": production_id,
            "method": method,
            "used_understanding_model": used_model,
            "opening": decomposition.get("opening") or {},
            "segments": [
                {
                    "id": item.get("id"),
                    "role": item.get("role"),
                    "start_ms": item.get("start_ms"),
                    "end_ms": item.get("end_ms"),
                }
                for item in (decomposition.get("segments") or [])
            ],
            "warnings": production.get("warnings") or [],
        }
        if not used_model:
            result["degraded"] = True
            result["note"] = "未调用或未配置视频理解模型，这是本地切分/降级结果，不是模型理解。"
        result.update(write_creative_context(self.settings, production))
        assert_no_forbidden_claim(result)
        return result

    def extract_landing_points(self, arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        production_id = self._pid(arguments, context)
        warnings = self._warnings(self._production(production_id))
        self.engine._prepare_landing(production_id, warnings)
        production = self._production(production_id)
        landing = production.get("landing_page")
        return {
            "production_id": production_id,
            "landing_page": landing,
            "extracted": bool(landing),
        }

    def search_video_references(self, arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        production_id = self._pid(arguments, context)
        production = self._production(production_id)
        brief = str(arguments.get("brief") or production.get("brief") or "")
        refs = self.library.retrieve(brief)
        intent = dict(production.get("intent") or {})
        intent["references"] = [
            {"path": item.get("path"), "why": item.get("why"), "category": item.get("category")}
            for item in refs
        ]
        self.db.update_production(production_id, {"intent": intent})
        production = self._production(production_id)
        context_paths = write_creative_context(self.settings, production)
        return {
            "production_id": production_id,
            "references": intent["references"],
            "count": len(refs),
            **context_paths,
        }

    def lock_timeline_opening(self, arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        production_id = self._pid(arguments, context)
        production = self._production(production_id)
        if not (production.get("decomposition") or {}).get("segments"):
            raise ProductionToolError("还没有拆解结果，无法锁定时间轴")
        timeline = local_script(production)
        assert_opening_locked(timeline, production)
        self.db.update_production(
            production_id,
            {
                "script": timeline,
                "current_stage": "script",
            },
        )
        opening = timeline.get("opening") or {}
        return {
            "production_id": production_id,
            "opening": opening,
            "total_duration_ms": timeline.get("total_duration_ms"),
            "shots": [
                {
                    "id": shot.get("id"),
                    "role": shot.get("role"),
                    "duration_ms": shot.get("duration_ms"),
                    "action": shot.get("action"),
                }
                for shot in (timeline.get("shots") or [])
            ],
        }

    def write_intent_and_scripts(self, arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        production_id = self._pid(arguments, context)
        production = self._production(production_id)
        timeline = production.get("script") or local_script(production)
        assert_opening_locked(timeline, production)
        intent = dict(arguments.get("intent") or production.get("intent") or {})
        if not intent.get("true_intent") and intent.get("goal"):
            intent["true_intent"] = intent.get("goal")
        if not intent.get("selling_points"):
            landing = production.get("landing_page") or {}
            intent["selling_points"] = list(
                landing.get("selling_points") or landing.get("benefits") or []
            )[:8]
        candidates = list(arguments.get("script_candidates") or arguments.get("candidates") or [])
        if len(candidates) != 3:
            raise ProductionToolError("必须恰好写入 3 个脚本候选")
        ids = [str(item.get("id") or "").strip() for item in candidates]
        if len(set(ids)) != 3 or any(not item for item in ids):
            raise ProductionToolError("三个脚本候选必须有互不相同的 id")
        locked_candidates = []
        for item in candidates:
            script = lock_script_to_timeline(item.get("script") or timeline, timeline, strict=True)
            assert_opening_locked(script, production)
            locked_candidates.append(
                {
                    "id": item["id"],
                    "title": item.get("title") or item["id"],
                    "angle": item.get("angle") or "",
                    "script": script,
                }
            )
        display = locked_candidates[0]["script"]
        updates: dict[str, Any] = {
            "intent": intent,
            "skeleton": arguments.get("skeleton") or production.get("skeleton"),
            "script": display,
            "script_candidates": locked_candidates,
            "selected_script_id": None,
            "candidate_videos": [],
            "status": "awaiting_script_approval",
            "current_stage": "script",
            "error": None,
        }
        self.db.update_production(production_id, updates)
        context_paths = write_creative_context(self.settings, self._production(production_id))
        return {
            "production_id": production_id,
            "status": "awaiting_script_approval",
            "candidate_ids": ids,
            "paused": True,
            "message": "3 个创意脚本已写入。请先向用户展示并确认脚本，确认后才生成候选视频。",
            **context_paths,
        }

    def get_production_state(self, arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        production_id = self._pid(arguments, context)
        production = self._production(production_id)
        output = production.get("output") or {}
        context_paths = creative_context_paths(self.settings, production)
        return {
            "production_id": production_id,
            "status": production.get("status"),
            "current_stage": production.get("current_stage"),
            "error": production.get("error"),
            "warnings": production.get("warnings") or [],
            "selected_script_id": production.get("selected_script_id"),
            "script_candidate_ids": [item.get("id") for item in (production.get("script_candidates") or [])],
            "candidate_video_ids": [item.get("id") for item in (production.get("candidate_videos") or [])],
            "opening": (
                (production.get("decomposition") or {}).get("opening")
                or (production.get("script") or {}).get("opening")
            ),
            "artifacts": {
                "video_path": output.get("video_path"),
                "srt_path": output.get("srt_path"),
                "video_url": output.get("video_url"),
                **context_paths,
            },
            "agent_trace": production.get("agent_trace") or [],
        }

    def update_production_progress(self, arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        production_id = self._pid(arguments, context)
        updates: dict[str, Any] = {}
        for key in ("current_stage", "status", "error"):
            if arguments.get(key) is not None:
                updates[key] = arguments.get(key)
        if "warnings" in arguments:
            updates["warnings"] = list(arguments.get("warnings") or [])
        if "message" in arguments:
            self.engine._emit(
                production_id,
                "progress",
                {"message": str(arguments.get("message") or ""), "stage": arguments.get("current_stage")},
            )
        if updates.get("status") == "completed" or updates.get("current_stage") == "done":
            raise ProductionToolError("只有计划完成门禁可以宣布完成")
        if updates:
            self.db.update_production(production_id, updates)
        production = self._production(production_id)
        return {
            "production_id": production_id,
            "status": production.get("status"),
            "current_stage": production.get("current_stage"),
        }

    def _validate_video_generation(
        self,
        production_id: str,
        script: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, str], str]:
        public, secrets = self.engine._settings_for_production(production_id)
        vg = public.get("video_generator") or {}
        provider = str(vg.get("provider") or "seedance")
        if provider not in REMOTE_VIDEO_PROVIDERS:
            raise ProductionToolError(
                f"视频生成只支持远端模型通道，当前为 {provider}，不能用本地关键帧伪生成"
            )
        generate_shots = [
            shot for shot in (script.get("shots") or []) if shot.get("action") == "generate"
        ]
        if generate_shots and not vg.get("base_url"):
            raise ProductionToolError(f"视频生成 Provider {provider} 未配置 base_url，无法出片")
        if generate_shots and not (secrets.get("video_generator") or ""):
            raise ProductionToolError(f"视频生成 Provider {provider} 未配置 API Key，无法出片")
        return public, secrets, provider

    def generate_candidate_videos(
        self,
        arguments: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        production_id = self._pid(arguments, context)
        production = self._production(production_id)
        candidates = list(production.get("script_candidates") or [])
        if len(candidates) != 3:
            raise ProductionToolError("必须恰好有 3 个脚本候选才能生成候选视频")
        for candidate in candidates:
            self._validate_video_generation(
                production_id,
                candidate.get("script") or {},
            )

        existing = {
            str(item.get("id") or ""): item
            for item in (production.get("candidate_videos") or [])
            if isinstance(item, dict)
        }
        if not (production.get("audio_separation") or {}).get("usable"):
            self.separate_audio(arguments, context)

        results_by_id: dict[str, dict[str, Any]] = {}
        for index, candidate in enumerate(candidates, start=1):
            candidate_id = str(candidate.get("id") or "")
            script = candidate.get("script") or {}
            previous = existing.get(candidate_id) or {}
            previous_path = Path(str(previous.get("video_path") or ""))
            if previous_path.is_file() and previous_path.stat().st_size > 0:
                results_by_id[candidate_id] = previous
                continue
            self.engine._emit(
                production_id,
                "progress",
                {
                    "stage": "generate",
                    "message": f"正在生成候选视频 {index}/3：{candidate.get('title') or candidate_id}",
                },
            )
            self.db.update_production(
                production_id,
                {
                    "script": script,
                    "selected_script_id": candidate_id,
                    "clips": [],
                    "output": None,
                    "status": "running",
                    "current_stage": "generate",
                    "error": None,
                },
            )
            self.generate_video_shots(arguments, context)
            composed = self.compose_timeline(arguments, context)
            current = self._production(production_id)
            output = dict(current.get("output") or composed.get("output") or {})
            video_path = Path(str(output.get("composed_video_path") or composed.get("video_path") or ""))
            if not video_path.is_file() or video_path.stat().st_size <= 0:
                raise ProductionToolError(f"候选 {candidate_id} 的视频文件缺失或为空")
            result = {
                "id": candidate_id,
                "script_id": candidate_id,
                "title": candidate.get("title") or candidate_id,
                "angle": candidate.get("angle") or "",
                "status": "ready",
                "script": script,
                "clips": list(current.get("clips") or []),
                "output": output,
                "video_path": str(video_path),
                "video_url": output.get("video_url") or composed.get("video_url") or "",
                "duration_ms": int(script.get("total_duration_ms") or 0),
                "width": int((current.get("source_video") or {}).get("width") or 0),
                "height": int((current.get("source_video") or {}).get("height") or 0),
            }
            results_by_id[candidate_id] = result
            ordered = [
                results_by_id[str(item.get("id") or "")]
                for item in candidates
                if str(item.get("id") or "") in results_by_id
            ]
            self.db.update_production(production_id, {"candidate_videos": ordered})
            self.engine._emit(
                production_id,
                "candidate_video_completed",
                {
                    "candidate_id": candidate_id,
                    "number": index,
                    "total": 3,
                    "video_url": result["video_url"],
                },
            )

        ordered = [
            results_by_id[str(item.get("id") or "")]
            for item in candidates
            if str(item.get("id") or "") in results_by_id
        ]
        if len(ordered) != 3:
            raise ProductionToolError("候选视频未全部生成，不能进入视频选择")
        first_script = candidates[0].get("script") or production.get("script")
        self.db.update_production(
            production_id,
            {
                "candidate_videos": ordered,
                "script": first_script,
                "clips": [],
                "output": None,
                "selected_script_id": None,
                "status": "awaiting_video_choice",
                "current_stage": "compose",
                "error": None,
            },
        )
        self.engine._emit(
            production_id,
            "awaiting_video_choice",
            {"message": "3 个候选视频已生成，请选择一个继续最终合成", "count": 3},
        )
        return {
            "production_id": production_id,
            "status": "awaiting_video_choice",
            "candidate_videos": [
                {
                    "id": item["id"],
                    "title": item["title"],
                    "video_url": item["video_url"],
                }
                for item in ordered
            ],
            "paused": True,
            "message": "3 个候选视频已生成，请选择其中一个继续最终合成。",
        }

    def generate_video_shots(self, arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        production_id = self._pid(arguments, context)
        production = self._production(production_id)
        if not production.get("selected_script_id"):
            raise ProductionToolError("还没有选定脚本，不能生成视频")
        if production.get("status") == "awaiting_script_choice":
            raise ProductionToolError("旧脚本选择状态已停用，请重新提交三套脚本")
        _public, _secrets, provider = self._validate_video_generation(
            production_id,
            production.get("script") or {},
        )
        generate_shots = [
            shot
            for shot in ((production.get("script") or {}).get("shots") or [])
            if shot.get("action") == "generate"
        ]
        warnings = self._warnings(production)
        self.engine._stage_generate(production_id, self._cancel(context), warnings)
        production = self._production(production_id)
        clips = production.get("clips") or []
        result = {
            "production_id": production_id,
            "provider": provider,
            "called_video_model": bool(generate_shots),
            "clips": [
                {"shot_id": item.get("shot_id"), "provider": item.get("provider"), "path": item.get("path")}
                for item in clips
            ],
        }
        assert_no_forbidden_claim(result)
        return result

    def separate_audio(self, arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        production_id = self._pid(arguments, context)
        production = self._production(production_id)
        source = Path((production.get("source_video") or {}).get("path") or "")
        if not source.is_file():
            raise ProductionToolError("参考视频不存在，无法分离音频")
        dest = self.engine.artifact_dir(production_id)
        result = separate_reference_audio(source, dest)
        result["bgm_volume"] = 0.12
        result["applied_to_output"] = False
        method = str(result.get("method") or "")
        if method in {"placeholder", "local_ms"}:
            result["note"] = "这是本地或占位分离，不是已确认的模型分轨结果。"
        self.db.update_production(production_id, {"audio_separation": result})
        assert_no_forbidden_claim(result)
        return {"production_id": production_id, **result}

    def _burn_subtitles(self, arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        production_id = self._pid(arguments, context)
        production = self._production(production_id)
        art = self.engine.artifact_dir(production_id)
        source = Path((production.get("source_video") or {}).get("path") or "")
        output = production.get("output") or {}
        video = Path(output.get("composed_video_path") or output.get("video_path") or (art / "final.mp4"))
        if not video.is_file():
            raise ProductionToolError("还没有成片，无法硬烧字幕")
        style = detect_subtitle_style(source, art) if source.is_file() else {"method": "default_safe", "aligned": False}
        if style.get("method") == "default_safe" or not style.get("aligned"):
            style = {**style, "aligned": False, "method": style.get("method") or "default_safe"}
        srt = art / "final.srt"
        write_shot_srt(srt, list((production.get("script") or {}).get("shots") or []))
        script_digest = digest([str(video), production.get("script")])[:16]
        burned = art / f"final-burned-{script_digest}.mp4"
        media.burn_subtitles(video, srt, burned, style)
        output = {
            **output,
            "video_path": str(burned),
            "subtitled_video_path": str(burned),
            "srt_path": str(srt),
            "video_url": self.engine.artifact_url(production_id, burned.name),
            "srt_url": self.engine.artifact_url(production_id, "final.srt"),
        }
        self.db.update_production(production_id, {"output": output, "subtitle_style": style})
        result = {
            "production_id": production_id,
            "method": style.get("method"),
            "aligned": bool(style.get("aligned")),
            "srt_path": str(srt),
            "video_path": str(burned),
            "output": output,
            "subtitle_style": style,
        }
        if not result["aligned"] or result["method"] == "default_safe":
            result["note"] = "字幕使用安全默认位置，未按参考对齐。"
        assert_no_forbidden_claim(result)
        return result

    def burn_subtitles(self, arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        production_id = self._pid(arguments, context)
        production = self._production(production_id)
        output = production.get("output") or {}
        runner = JobRunner(self.db, production_id, self._cancel(context))
        inputs = {"revision": revision(production),
                  "video_path": output.get("composed_video_path") or output.get("video_path")}
        result = runner.run("burn_subtitles", inputs, lambda job: self._burn_subtitles(arguments, context))
        runner.check_cancelled()
        self.db.update_production(
            production_id,
            {"output": result["output"], "subtitle_style": result["subtitle_style"]},
        )
        return result

    def compose_timeline(self, arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        production_id = self._pid(arguments, context)
        production = self._production(production_id)
        runner = JobRunner(self.db, production_id, self._cancel(context))
        inputs = {"revision": revision(production), "clips": production.get("clips"),
                  "audio_separation": {key: (production.get("audio_separation") or {}).get(key)
                                       for key in ("usable", "bgm_path", "bgm_volume")}}

        def compose(job):
            self.engine._stage_compose(production_id, self._cancel(context), self._warnings(production))
            runner.check_cancelled()
            output = dict(self._production(production_id).get("output") or {})
            source = Path(output.get("video_path") or "")
            if not source.is_file() or source.stat().st_size <= 0:
                raise ProductionToolError("合成结束但成片文件缺失或为空")
            dest = self.engine.artifact_dir(production_id) / f"composed-{job['id']}.mp4"
            shutil.copyfile(source, dest)
            output.update({"video_path": str(dest), "composed_video_path": str(dest),
                           "video_url": self.engine.artifact_url(production_id, dest.name)})
            output.pop("subtitled_video_path", None)
            return {"production_id": production_id, "status": "running", "video_path": str(dest),
                    "video_url": output["video_url"], "output": output}

        result = runner.run("compose_timeline", inputs, compose)
        runner.check_cancelled()
        self.db.update_production(production_id, {"output": result["output"]})
        return result

    def handle_production_followup(self, arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        production_id = self._pid(arguments, context)
        production = self._production(production_id)
        text = str(arguments.get("text") or arguments.get("brief") or production.get("brief") or "")
        vague = _VAGUE_FOLLOWUP_RE.search(text)
        selected = parse_script_selection(text, production.get("script_candidates") or [])
        if vague and not selected:
            self.engine._emit(
                production_id,
                "clarification.requested",
                {"message": "请补充想改的文案、节奏或画面", "production_id": production_id},
            )
            return {
                "action": "clarify",
                "production_id": production_id,
                "paused": True,
                "message": "已停在当前任务等待澄清，不会从 ingest 重来。",
            }
        action, detail = classify_followup(production, text)
        if action == "select_video":
            chosen = next(
                (
                    item
                    for item in production.get("candidate_videos") or []
                    if str(item.get("id") or "") == detail
                ),
                None,
            )
            if not chosen:
                raise ProductionToolError("候选视频不存在")
            self.db.update_production(
                production_id,
                {
                    "script": chosen.get("script") or production.get("script"),
                    "selected_script_id": chosen["id"],
                    "clips": chosen.get("clips") or [],
                    "output": chosen.get("output") or {},
                    "error": None,
                },
            )
            return {
                "action": "select_video",
                "production_id": production_id,
                "video_id": chosen["id"],
                "paused": True,
                "resume_from": "finalize",
                "message": "已记录所选视频，继续最终合成即可出片。",
            }
        if detail == "ingest":
            detail = "script" if production.get("script_candidates") else "intent"
        return {
            "action": "resume",
            "production_id": production_id,
            "resume_from": detail,
            "message": f"将在同一任务上从 {detail} 继续，不会新建任务。",
        }


def register_production_tools(registry: ToolRegistry, host: ProductionToolHost) -> None:
    specs = {
        "ingest_reference_video": (
            "接入参考视频",
            "Copy and probe the reference video into the production artifact directory.",
        ),
        "understand_reference_video": (
            "理解并切分参考视频",
            "Decompose the reference video and finalize the opening block. Missing keys degrade honestly.",
        ),
        "extract_landing_points": (
            "提取落地页卖点",
            "Extract landing-page selling points when a URL or pasted body is present.",
        ),
        "search_video_references": ("检索参考脚本", "Retrieve video-references for this production brief."),
        "lock_timeline_opening": (
            "锁定时间轴和开场连续块",
            "Lock the shared timeline and the continuous shot_opening block.",
        ),
        "write_intent_and_scripts": (
            "写入意图与三脚本候选",
            "Persist intent, skeleton, and exactly three script candidates, then continue to video generation.",
        ),
        "get_production_state": ("查询任务状态与产物", "Read production status, selected script, and artifact paths."),
        "update_production_progress": ("更新任务进度", "Write progress, status, or a short progress message."),
        "generate_candidate_videos": (
            "生成三个候选视频",
            "Generate and compose all three script candidates, then pause for final video selection.",
        ),
        "generate_video_shots": (
            "生成视频镜头",
            "Generate selected-script shots via Seedance / OpenAI Compatible / Generic HTTP only.",
        ),
        "separate_audio": ("分离参考音频", "Split reference audio into vocals and BGM."),
        "burn_subtitles": ("硬烧字幕", "Burn subtitles onto the current final video."),
        "compose_timeline": ("合成时间线", "Concatenate generated or reused clips into final.mp4 with FFmpeg."),
        "handle_production_followup": (
            "处理同一任务追问",
            "Classify and apply a follow-up on the same production: clarify, select, or resume.",
        ),
    }
    handlers = {name: getattr(host, name) for name in CAPABILITY_TOOLS}
    for name, (title, description) in specs.items():
        properties: dict[str, Any] = {
            "production_id": {"type": "string", "maxLength": 80},
        }
        if name == "write_intent_and_scripts":
            properties.update(
                {
                    "intent": {"type": "object"},
                    "skeleton": {"type": "object"},
                    "script_candidates": {"type": "array"},
                }
            )
        if name == "handle_production_followup":
            properties["text"] = {"type": "string"}
        if name == "search_video_references":
            properties["brief"] = {"type": "string"}
        if name == "update_production_progress":
            properties.update(
                {
                    "current_stage": {"type": "string"},
                    "status": {"type": "string"},
                    "message": {"type": "string"},
                    "warnings": {"type": "array", "items": {"type": "string"}},
                }
            )
        registry.register(
            ToolDefinition(
                name=name,
                title=title,
                description=description,
                input_schema={
                    "type": "object",
                    "properties": properties,
                    "required": ["production_id"],
                    "additionalProperties": True,
                },
                handler=handlers[name],
                model_visible=False,
            )
        )
