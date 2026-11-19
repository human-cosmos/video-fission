from __future__ import annotations

import copy
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

from . import media, providers
from .config import Settings
from .db import Database
from .logging_utils import clip_text, file_summary, log_context, safe_url
from .paths import production_artifact_dir
from .references import ReferenceLibrary
from .replace import apply_replace_plan, resolve_replace_plan, strip_rewrite_instructions
from .settings_store import SettingsStore
from .script_agent import ScriptAgentError
from .understand.opening import opening_block_generate

logger = logging.getLogger(__name__)

STAGES = ("ingest", "decompose", "intent", "script", "generate", "compose")
REMOTE_VIDEO_PROVIDERS = {"openai_compatible", "generic_http", "seedance"}


class PipelineCancelled(RuntimeError):
    pass


class PipelineEngine:
    def __init__(self, settings: Settings, db: Database, store: SettingsStore):
        self.settings = settings
        self.db = db
        self.store = store
        self.library = ReferenceLibrary(settings.project_root)
        self.script_agent = None

    def artifact_dir(self, production_id: str) -> Path:
        production = self.db.get_production(production_id)
        if not production:
            raise ValueError("创作任务不存在")
        path = production_artifact_dir(
            self.settings.artifacts_dir,
            production.get("user_id"),
            production_id,
        )
        legacy_path = self.settings.artifacts_dir / "productions" / production_id
        if production.get("user_id") and not path.exists() and legacy_path.exists():
            path = legacy_path
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _settings_for_production(
        self,
        production_id: str,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        production = self.db.get_production(production_id)
        user_id = str((production or {}).get("user_id") or "").strip() or None
        return self.store.raw(user_id)

    def artifact_url(self, production_id: str, name: str) -> str:
        return f"/api/productions/{production_id}/artifacts/{name}"

    def run(
        self,
        production_id: str,
        cancel: threading.Event | None = None,
        resume_from: str | None = None,
    ) -> dict[str, Any]:
        raise RuntimeError(
            "出片编排已改由 Production Agent 负责，PipelineEngine 阶段循环不再作为执行入口"
        )

    def _stage_ingest(self, production_id: str, cancel: threading.Event, warnings: list[str]) -> None:
        production = self._get(production_id)
        source = production["source_video"]
        src = Path(source["path"])
        if not src.is_file():
            raise ValueError("参考视频不存在")
        dest_dir = self.artifact_dir(production_id)
        dest = dest_dir / f"source{src.suffix or '.mp4'}"
        if src.resolve() != dest.resolve():
            dest.write_bytes(src.read_bytes())
        info = media.probe(dest)
        logger.info(
            "pipeline.ingest source=%s size=%s duration_ms=%s resolution=%sx%s fps=%.3f audio=%s",
            file_summary(dest),
            dest.stat().st_size,
            info["duration_ms"],
            info["width"],
            info["height"],
            float(info["fps"] or 0),
            info["has_audio"],
        )
        source.update(
            {
                "path": str(dest),
                "duration_ms": info["duration_ms"],
                "width": info["width"],
                "height": info["height"],
                "fps": info["fps"],
            }
        )
        self.db.update_production(production_id, {"source_video": source})

    def _stage_decompose(self, production_id: str, cancel: threading.Event, warnings: list[str]) -> None:
        from .understand.service import run_decompose

        production = self._get(production_id)
        public, secrets = self._settings_for_production(production_id)
        decomposition = run_decompose(
            production,
            self.artifact_dir(production_id),
            public,
            secrets,
            warnings,
            emit=lambda kind, payload: self._emit(production_id, kind, payload),
            cancel=cancel,
            allow_local_fallback=self.settings.allow_local_copy_fallback,
        )
        logger.info(
            "pipeline.decompose_completed provider=%s model=%s segments=%s summary_chars=%s warnings=%s",
            decomposition.get("provider") or "-",
            decomposition.get("model") or "-",
            len(decomposition.get("segments") or []),
            len(str(decomposition.get("summary") or "")),
            len(warnings),
        )
        self.db.update_production(production_id, {"decomposition": decomposition, "warnings": warnings})

    def _refine_with_multimodal(
        self,
        production: dict[str, Any],
        segments: list[dict[str, Any]],
        art: Path,
        mm: dict[str, Any],
        api_key: str,
    ) -> dict[str, Any] | None:
        frames = sorted(art.glob("seg_*_frame_01.jpg"))[:16]
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "请根据参考视频关键帧和已有本地切分，返回严格 JSON："
                    '{"summary": str, "segments":[{"id","role","start_ms","end_ms",'
                    '"summary","visual","audio","on_screen_text","reuse_recommended"}]}。'
                    "角色只能是 hook/value/cta/other。时间必须覆盖整段且不重叠。"
                    f"创作说明：{production['brief']}\n本地切分：{segments}"
                ),
            }
        ]
        content.extend(providers.encode_image(path) for path in frames)
        raw = providers.chat_completions(
            mm.get("base_url") or "",
            api_key,
            mm.get("model") or "",
            [{"role": "user", "content": content}],
            int(mm.get("timeout_seconds") or 120),
        )
        data = providers.extract_json(raw)
        duration_ms = int(production["source_video"]["duration_ms"])
        incoming = data.get("segments") or []
        cleaned = normalize_model_segments(incoming, duration_ms, segments)
        logger.info(
            "llm.segment_refine_completed model=%s raw_segments=%s normalized_segments=%s summary_chars=%s",
            mm.get("model") or "-",
            len(incoming),
            len(cleaned),
            len(str(data.get("summary") or "")),
        )
        return {
            "summary": str(data.get("summary") or production_summary_fallback(segments, duration_ms)),
            "provider": "openai_compatible",
            "model": mm.get("model") or "",
            "segments": cleaned,
        }

    def _stage_intent(self, production_id: str, cancel: threading.Event, warnings: list[str]) -> None:
        self._prepare_landing(production_id, warnings)
        production = self._get(production_id)
        decomposition = production.get("decomposition") or {}
        segments = decomposition.get("segments") or []
        plan = resolve_replace_plan(
            production.get("replace_policy"),
            segments,
            production.get("brief") or "",
            opening=decomposition.get("opening"),
        )
        logger.info(
            "pipeline.intent_plan mode=%s resolved_segments=%s reasons=%s",
            (plan.get("policy") or {}).get("mode") or "-",
            len(plan.get("resolved_segment_ids") or []),
            len(plan.get("reasons") or []),
        )
        self.db.update_production(production_id, {"replace_plan": plan})
        production = self._get(production_id)
        refs = self.library.retrieve(production["brief"])
        logger.info("pipeline.intent_references count=%s", len(refs))
        for item in refs:
            path = self.settings.project_root / item["path"]
            if not path.is_file():
                raise ValueError(f"参考文件不存在: {item['path']}")
        if self.script_agent is not None:
            try:
                production_for_agent = {
                    **production,
                    "_artifact_dir": str(self.artifact_dir(production_id)),
                }
                data = self.script_agent.generate_intent(
                    production_for_agent,
                    refs,
                    cancel=cancel,
                )
                intent, skeleton = self._validate_agent_intent(data)
            except Exception as exc:
                if cancel.is_set():
                    raise PipelineCancelled("任务已取消") from exc
                if self.settings.allow_local_copy_fallback:
                    warnings.append(f"测试兼容模式：脚本 Agent 失败，回退本地规划器：{exc.__class__.__name__}")
                    intent, skeleton = local_intent(production["brief"], production.get("decomposition") or {}, refs)
                    logger.warning("pipeline.intent_agent_failed test_fallback=local", exc_info=True)
                else:
                    logger.exception("pipeline.intent_agent_failed")
                    raise ScriptAgentError(f"Agent 意图生成失败：{str(exc)[:500]}") from exc
        elif self.settings.allow_local_copy_fallback:
            warnings.append("测试兼容模式：intent 使用本地文案规划器")
            intent, skeleton = local_intent(production["brief"], production.get("decomposition") or {}, refs)
        else:
            raise ScriptAgentError("intent 必须由 Codex Agent 使用项目 Skills 生成")
        # Strip excerpts before persisting references (contract only has path/why)
        intent["references"] = [{"path": item["path"], "why": item["why"]} for item in refs]
        logger.info(
            "pipeline.intent_completed goal_chars=%s audience_chars=%s promise_chars=%s skeleton_chars=%s",
            len(str(intent.get("goal") or "")),
            len(str(intent.get("audience") or "")),
            len(str(intent.get("promise") or "")),
            len(str(skeleton)),
        )
        self.db.update_production(production_id, {"intent": intent, "skeleton": skeleton, "warnings": warnings})

    @staticmethod
    def _validate_agent_intent(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        intent = data.get("intent") or {}
        skeleton = data.get("skeleton") or {}
        required_intent = {
            "goal": str(intent.get("goal") or "").strip(),
            "audience": str(intent.get("audience") or "").strip(),
            "promise": str(intent.get("promise") or "").strip(),
        }
        missing = [key for key, value in required_intent.items() if not value]
        assumptions = [str(item).strip() for item in (intent.get("assumptions") or []) if str(item).strip()]
        if not assumptions:
            missing.append("assumptions")
        required_skeleton = {
            key: str(skeleton.get(key) or "").strip()
            for key in ("one_liner", "hook", "value", "cta")
        }
        missing.extend(
            [f"skeleton.{key}" for key, value in required_skeleton.items() if not value]
        )
        if missing:
            raise ScriptAgentError(f"Agent 意图字段不完整：{', '.join(missing)}")
        merged_intent = {**required_intent, "assumptions": assumptions}
        merged_skeleton = required_skeleton
        logger.info(
            "agent.intent_completed goal_chars=%s promise_chars=%s assumptions=%s",
            len(merged_intent["goal"]),
            len(merged_intent["promise"]),
            len(merged_intent["assumptions"]),
        )
        return merged_intent, merged_skeleton

    def _stage_script(self, production_id: str, cancel: threading.Event, warnings: list[str]) -> None:
        from .script_flow import build_local_candidates, lock_script_to_timeline

        self._prepare_landing(production_id, warnings)
        production = self._get(production_id)
        refs = (production.get("intent") or {}).get("references") or []
        full_texts: list[str] = []
        for item in refs[: max(3, len(refs))]:
            try:
                full_texts.append(self.library.read_full(item["path"])[:8000])
            except Exception:
                continue
        if len(full_texts) < 3 and refs:
            extra = self.library.retrieve(production["brief"])
            for item in extra:
                if len(full_texts) >= 3:
                    break
                try:
                    full_texts.append(self.library.read_full(item["path"])[:8000])
                except Exception:
                    continue
        if len(full_texts) < 3:
            warnings.append("完整参考脚本不足 3 篇，已尽可能读取现有参考")
            self._emit(production_id, "warning", {"stage": "script", "message": warnings[-1]})
        logger.info(
            "pipeline.script_inputs references=%s full_texts=%s reference_chars=%s",
            len(refs),
            len(full_texts),
            sum(len(text) for text in full_texts),
        )
        timeline = local_script(production)
        if self.script_agent is not None:
            try:
                production_for_agent = {
                    **production,
                    "_artifact_dir": str(self.artifact_dir(production_id)),
                }
                data = self.script_agent.generate_script_candidates(
                    production_for_agent,
                    timeline,
                    [item.get("path") or "" for item in refs],
                    cancel=cancel,
                )
                candidates = self._normalize_agent_candidates(data, timeline)
            except Exception as exc:
                if cancel.is_set():
                    raise PipelineCancelled("任务已取消") from exc
                if self.settings.allow_local_copy_fallback:
                    warnings.append(f"测试兼容模式：脚本 Agent 失败，回退本地规划器：{exc.__class__.__name__}")
                    candidates = build_local_candidates(production, timeline)
                    logger.warning("pipeline.script_agent_failed test_fallback=local", exc_info=True)
                else:
                    logger.exception("pipeline.script_agent_failed")
                    raise ScriptAgentError(f"Agent 分镜生成失败：{str(exc)[:500]}") from exc
        elif self.settings.allow_local_copy_fallback:
            warnings.append("测试兼容模式：script 使用本地文案规划器")
            candidates = build_local_candidates(production, timeline)
        else:
            raise ScriptAgentError("script 必须由 Codex Agent 使用项目 Skills 生成")
        for item in candidates:
            locked = lock_script_to_timeline(
                item.get("script") or timeline,
                timeline,
                strict=not self.settings.allow_local_copy_fallback,
            )
            item["script"] = apply_replace_plan(locked, production)
        display = (candidates[0]["script"] if candidates else timeline)
        logger.info(
            "pipeline.script_completed candidates=%s shots=%s display_duration_ms=%s",
            len(candidates),
            len(display.get("shots") or []),
            display.get("total_duration_ms"),
        )
        self.db.update_production(
            production_id,
            {
                "script": display,
                "script_candidates": candidates,
                "selected_script_id": None,
                "warnings": warnings,
                "status": "awaiting_script_choice",
                "current_stage": "script",
                "error": None,
            },
        )

    def _normalize_agent_candidates(
        self,
        data: dict[str, Any],
        timeline: dict[str, Any],
    ) -> list[dict[str, Any]]:
        from .script_flow import CANDIDATE_SPECS

        incoming = data.get("candidates") or data.get("scripts") or []
        if not isinstance(incoming, list) or len(incoming) < len(CANDIDATE_SPECS):
            raise ScriptAgentError(f"Agent 必须返回 {len(CANDIDATE_SPECS)} 个候选")
        merged = []
        for index, spec in enumerate(CANDIDATE_SPECS):
            source = incoming[index]
            if not isinstance(source, dict):
                raise ScriptAgentError(f"第 {index + 1} 个候选不是对象")
            merged.append(
                {
                    "id": spec["id"],
                    "title": self._required_agent_text(source.get("title"), f"candidates[{index}].title"),
                    "angle": self._required_agent_text(source.get("angle"), f"candidates[{index}].angle"),
                    "script": self._agent_candidate_script(
                        source.get("script") or source,
                        timeline,
                        index,
                    ),
                }
            )
        logger.info(
            "agent.script_candidates_completed raw_candidates=%s candidates=%s",
            len(incoming),
            len(merged),
        )
        return merged

    @staticmethod
    def _required_agent_text(value: Any, field: str) -> str:
        text = strip_duration_phrase(str(value or "")).strip()
        if not text or _is_template_copy(text):
            raise ScriptAgentError(f"Agent 字段缺失或仍是模板：{field}")
        return text

    def _agent_candidate_script(
        self,
        source: Any,
        timeline: dict[str, Any],
        candidate_index: int,
    ) -> dict[str, Any]:
        if not isinstance(source, dict):
            raise ScriptAgentError(f"第 {candidate_index + 1} 个候选的 script 不是对象")
        incoming = source.get("shots")
        timeline_shots = timeline.get("shots") or []
        if not isinstance(incoming, list) or len(incoming) < len(timeline_shots):
            raise ScriptAgentError(
                f"第 {candidate_index + 1} 个 Agent 候选镜头数不足：需要 {len(timeline_shots)}，"
                f"实际 {len(incoming) if isinstance(incoming, list) else 0}"
            )
        by_id = {
            str(item.get("id") or ""): item
            for item in incoming
            if isinstance(item, dict) and item.get("id")
        }
        locked = copy.deepcopy(timeline)
        for index, shot in enumerate(locked.get("shots") or []):
            item = by_id.get(str(shot.get("id") or "")) or incoming[index]
            if not isinstance(item, dict):
                raise ScriptAgentError(f"第 {candidate_index + 1} 个候选镜头 {index + 1} 无效")
            field_base = f"candidates[{candidate_index}].shots[{index}]"
            visual = self._required_agent_text(item.get("visual"), f"{field_base}.visual")
            role = str(shot.get("role") or "other")
            voiceover = strip_duration_phrase(str(item.get("voiceover") or "")).strip()
            on_screen_text = strip_duration_phrase(str(item.get("on_screen_text") or "")).strip()
            if role != "other":
                if not voiceover or _is_template_copy(voiceover):
                    raise ScriptAgentError(f"Agent 口播缺失或仍是模板：{field_base}.voiceover")
                if not on_screen_text or on_screen_text.lower() in _GENERIC_ON_SCREEN:
                    raise ScriptAgentError(f"Agent 花字缺失或仍是模板：{field_base}.on_screen_text")
            prompt = str(item.get("prompt") or "").strip()
            if shot.get("action") == "generate" and not prompt:
                raise ScriptAgentError(f"生成镜头缺少视频 prompt：{field_base}.prompt")
            shot["visual"] = visual
            shot["voiceover"] = voiceover if role != "other" else ""
            shot["on_screen_text"] = on_screen_text if role != "other" else ""
            shot["prompt"] = prompt if shot.get("action") == "generate" else ""
        locked["total_duration_ms"] = sum(int(shot["duration_ms"]) for shot in locked["shots"])
        audio_plan = source.get("audio_plan")
        locked["audio_plan"] = {
            "voiceover_language": str((audio_plan or {}).get("voiceover_language") or "zh"),
            "bgm": "model_generated",
            "subtitles": False,
        }
        return locked

    def _stage_generate(self, production_id: str, cancel: threading.Event, warnings: list[str]) -> None:
        production = self._get(production_id)
        source = production["source_video"]
        script = apply_replace_plan(production.get("script") or {}, production)
        self.db.update_production(production_id, {"script": script})
        segments = {seg["id"]: seg for seg in (production.get("decomposition") or {}).get("segments") or []}
        width, height = media.target_dimensions(int(source["width"]), int(source["height"]))
        fps = float(source["fps"] or 30)
        src = Path(source["path"])
        art = self.artifact_dir(production_id)
        public, secrets = self._settings_for_production(production_id)
        vg = public["video_generator"]
        provider = str(vg.get("provider") or "seedance")
        if provider not in REMOTE_VIDEO_PROVIDERS:
            raise ValueError(f"视频生成只支持远端模型通道，当前为 {provider}")
        generate_shots = [shot for shot in script.get("shots") or [] if shot.get("action") == "generate"]
        if generate_shots and not vg.get("base_url"):
            raise ValueError(f"视频生成 Provider {provider} 未配置 base_url")
        input_contents = (
            self._seedance_input_contents(production_id, public, secrets, warnings)
            if provider == "seedance"
            else []
        )
        logger.info(
            "pipeline.generate_config provider=%s model=%s target=%sx%s "
            "fps=%.3f reference_inputs=%s shots=%s generated=%s",
            provider,
            vg.get("model") or "-",
            width,
            height,
            fps,
            ",".join(str(item.get("role") or item.get("type") or "?") for item in input_contents) or "-",
            len(script.get("shots") or []),
            len(generate_shots),
        )
        clips: list[dict[str, Any]] = []
        for shot in script.get("shots") or []:
            self._raise_if_cancelled(cancel)
            dest = art / f"{shot['id']}.mp4"
            duration_ms = int(shot["duration_ms"])
            action_label = "复用原片" if shot["action"] == "reuse" else "生成新镜头"
            logger.info(
                "generate.shot_started shot_id=%s action=%s duration_ms=%s source_segment=%s",
                shot["id"],
                shot.get("action") or "-",
                duration_ms,
                shot.get("source_segment_id") or "-",
            )
            self._emit(
                production_id,
                "stage_started",
                {
                    "stage": "generate",
                    "step": shot["id"],
                    "message": f"{shot['id']} 开始{action_label}",
                    "details": {"duration_ms": duration_ms},
                },
            )
            start_ms, _end_ms, segment = _reuse_range(shot, segments, production)
            if shot["action"] == "reuse":
                if _end_ms <= start_ms:
                    raise ValueError(f"{shot['id']} 缺少可复用源片段")
                media.cut_segment(src, dest, start_ms, _end_ms, width, height, fps, True)
                provider_name = "reference_reuse"
                logger.info(
                    "generate.shot_reused shot_id=%s source_range=%s-%sms output=%s",
                    shot["id"],
                    start_ms,
                    _end_ms,
                    file_summary(dest),
                )
            else:
                provider_name = provider
                try:
                    providers.generate_remote_video(
                        provider_name,
                        vg.get("base_url") or "",
                        secrets.get("video_generator") or "",
                        vg.get("model") or "",
                        self._shot_generate_prompt(shot, production),
                        duration_ms,
                        width,
                        height,
                        fps,
                        int(vg.get("timeout_seconds") or 600),
                        dest,
                        source=vg.get("source") or "",
                        resolution=vg.get("resolution") or "720p",
                        cancel=cancel,
                        input_contents=input_contents,
                    )
                    self._raise_if_cancelled(cancel)
                    if not dest.is_file() or dest.stat().st_size <= 0:
                        raise providers.ProviderError("远端 Provider 返回了空文件")
                    logger.info(
                        "generate.remote_completed shot_id=%s provider=%s raw_output=%s",
                        shot["id"],
                        provider_name,
                        file_summary(dest),
                    )
                    normalized = dest.with_name(dest.stem + "_normalized.mp4")
                    media.normalize_video_clip(
                        dest,
                        normalized,
                        duration_ms,
                        width,
                        height,
                        fps,
                    )
                    normalized.replace(dest)
                    logger.info(
                        "generate.remote_normalized shot_id=%s provider=%s output=%s",
                        shot["id"],
                        provider_name,
                        file_summary(dest),
                    )
                    self._raise_if_cancelled(cancel)
                except providers.ProviderCancelled as exc:
                    raise PipelineCancelled("任务已取消") from exc
                except Exception as exc:
                    if cancel.is_set():
                        raise PipelineCancelled("任务已取消") from exc
                    logger.error(
                        "generate.remote_failed shot_id=%s provider=%s fallback=disabled",
                        shot["id"],
                        provider_name,
                        exc_info=True,
                    )
                    raise RuntimeError(
                        f"{shot['id']} 视频模型生成失败，未回退本地：{exc}"
                    ) from exc
            info = media.probe(dest)
            if info["duration_ms"] <= 0 or abs(info["duration_ms"] - duration_ms) > 150:
                raise RuntimeError(
                    f"{shot['id']} 片段时长异常：期望 {duration_ms}ms，实际 {info['duration_ms']}ms"
                )
            if dest.stat().st_size < 1000:
                raise RuntimeError(f"{shot['id']} 生成了空文件")
            if shot["action"] == "generate" and not info.get("has_audio"):
                raise RuntimeError(f"{shot['id']} 视频模型输出缺少音轨")
            if shot["action"] == "reuse" and not info.get("has_audio"):
                with_audio = dest.with_name(dest.stem + "_with_audio.mp4")
                media.add_silence_audio(dest, with_audio, duration_ms)
                with_audio.replace(dest)
                info = media.probe(dest)
                logger.info(
                    "generate.reuse_audio_added shot_id=%s duration_ms=%s output=%s",
                    shot["id"],
                    duration_ms,
                    file_summary(dest),
                )
            logger.info(
                "generate.shot_completed shot_id=%s provider=%s duration_ms=%s size=%s audio=%s",
                shot["id"],
                provider_name,
                info["duration_ms"],
                dest.stat().st_size,
                info.get("has_audio"),
            )
            clips.append(
                {
                    "shot_id": shot["id"],
                    "path": str(dest),
                    "url": self.artifact_url(production_id, dest.name),
                    "provider": provider_name,
                    "duration_ms": info["duration_ms"],
                }
            )
            self._emit(
                production_id,
                "stage_completed",
                {
                    "stage": "generate",
                    "step": shot["id"],
                    "message": f"{shot['id']} 已输出",
                    "details": {
                        "provider": provider_name,
                        "duration_ms": info["duration_ms"],
                        "file": dest.name,
                    },
                },
            )
        logger.info(
            "pipeline.generate_completed clips=%s providers=%s",
            len(clips),
            ",".join(sorted({str(item.get("provider") or "-") for item in clips})),
        )
        self.db.update_production(production_id, {"clips": clips, "warnings": warnings})

    def _stage_compose(self, production_id: str, cancel: threading.Event, warnings: list[str]) -> None:
        production = self._get(production_id)
        script = production.get("script") or {}
        clips = production.get("clips") or []
        art = self.artifact_dir(production_id)
        clip_paths = [Path(item["path"]) for item in clips]
        if not clip_paths:
            raise ValueError("没有可合成的片段")
        shots = script.get("shots") or []
        source_info = production.get("source_video") or {}
        fps = float(source_info.get("fps") or 30)
        opening_shot_id = str((script.get("opening") or {}).get("shot_id") or "")
        faded: list[Path] = []
        total_ms = int(script.get("total_duration_ms") or source_info["duration_ms"])
        for stale_name in ("final.srt", "burned.mp4", "with-bgm.mp4"):
            (art / stale_name).unlink(missing_ok=True)
        logger.info(
            "pipeline.compose_config clips=%s total_ms=%s fps=%.3f video_audio=model_or_reference",
            len(clips),
            total_ms,
            fps,
        )
        self._emit(
            production_id,
            "stage_started",
            {
                "stage": "compose",
                "step": "assemble",
                "message": f"拼接 {len(clips)} 个镜头",
            },
        )
        for index, item in enumerate(clips):
            path = Path(item["path"])
            fade_in, fade_out = shot_edge_fades(index, shots, opening_shot_id)
            duration_ms = int((shots[index]["duration_ms"] if index < len(shots) else item.get("duration_ms")) or 1)
            if fade_in or fade_out:
                faded_path = art / f"{item['shot_id']}_fade.mp4"
                media.apply_edge_fades(path, faded_path, duration_ms, fps, fade_in, fade_out)
                faded.append(faded_path)
                logger.info(
                    "compose.fade_applied shot_id=%s fade_in=%s fade_out=%s output=%s",
                    item.get("shot_id") or index,
                    fade_in,
                    fade_out,
                    file_summary(faded_path),
                )
            else:
                faded.append(path)
        concat_path = art / "concat.mp4"
        media.concat_clips(faded, concat_path)
        current = concat_path
        logger.info("compose.concat_completed clips=%s output=%s", len(faded), file_summary(concat_path))
        self._emit(
            production_id,
            "stage_completed",
            {
                "stage": "compose",
                "step": "assemble",
                "message": "镜头拼接完成",
                "details": {"clips": len(clips), "duration_ms": total_ms},
            },
        )
        self._emit(
            production_id,
            "stage_started",
            {
                "stage": "compose",
                "step": "finalize",
                "message": "对齐音画并校验成片",
            },
        )
        final_path = art / "final.mp4"
        try:
            media.fit_av_duration(current, final_path, total_ms, fps)
            logger.info(
                "compose.duration_aligned source=%s target_ms=%s output=%s",
                file_summary(current),
                total_ms,
                file_summary(final_path),
            )
        except Exception as exc:
            warnings.append(f"音画对齐失败，已保留当前成片：{exc.__class__.__name__}")
            logger.warning("compose.duration_align_failed preserve_current", exc_info=True)
            self._emit(production_id, "warning", {"stage": "compose", "message": warnings[-1]})
            if current != final_path:
                final_path.write_bytes(current.read_bytes())
        info = media.probe(final_path)
        logger.info(
            "pipeline.compose_completed output=%s duration_ms=%s resolution=%sx%s fps=%.3f size=%s audio=%s",
            file_summary(final_path),
            info["duration_ms"],
            info["width"],
            info["height"],
            float(info["fps"] or 0),
            final_path.stat().st_size,
            info["has_audio"],
        )
        if abs(info["duration_ms"] - total_ms) > 500:
            warnings.append(
                f"成片时长 {info['duration_ms']}ms 与脚本 {total_ms}ms 偏差超过 0.5s"
            )
        self._emit(
            production_id,
            "stage_completed",
            {
                "stage": "compose",
                "step": "finalize",
                "message": "成片校验完成",
                "details": {
                    "duration_ms": info["duration_ms"],
                    "file": final_path.name,
                },
            },
        )
        output = {
            "video_path": str(final_path),
            "video_url": self.artifact_url(production_id, "final.mp4"),
        }
        self.db.update_production(production_id, {"output": output, "warnings": warnings})

    def _prepare_landing(self, production_id: str, warnings: list[str]) -> None:
        from .landing import analyze_landing

        production = self._get(production_id)
        landing = analyze_landing(production.get("brief") or "")
        if not landing:
            logger.info("landing.skipped reason=no_url_or_body")
            return
        status = landing.get("parse_status") or ""
        logger.info(
            "landing.completed status=%s method=%s product_chars=%s selling_points=%s source=%s",
            status,
            landing.get("method") or "-",
            len(str(landing.get("product") or "")),
            len(landing.get("selling_points") or []),
            safe_url(str(landing.get("source_url") or "")),
        )
        if status == "failed":
            message = "落地页抓取失败，可在对话里粘贴页面正文后再解析"
            warnings.append(message)
            self._emit(production_id, "warning", {"stage": "intent", "message": message})
        elif status == "partial":
            message = "落地页仅部分解析，未当作完整卖点"
            warnings.append(message)
            self._emit(production_id, "warning", {"stage": "intent", "message": message})
        self.db.update_production(production_id, {"landing_page": landing, "warnings": warnings})

    def _landing_prompt(self, production: dict[str, Any]) -> str:
        from .landing import landing_prompt_block

        return landing_prompt_block(production.get("landing_page"))

    def _shot_generate_prompt(self, shot: dict[str, Any], production: dict[str, Any]) -> str:
        voiceover = str(shot.get("voiceover") or "").strip()
        base = str(shot.get("prompt") or shot.get("visual") or production.get("brief") or "").strip()
        parts = [
            base,
            (
                "由视频模型直接生成完整语音和音乐/环境音，输出文件必须带同步音轨；"
                f"口播原文：{voiceover or '（按镜头内容生成自然口播）'}"
            ),
            "画面内不得生成字幕、标题、花字、说明文字或任何后期叠加文字。",
            self._landing_prompt(production),
        ]
        return "\n".join(part for part in parts if part)

    def _seedance_input_contents(
        self,
        production_id: str,
        public: dict[str, Any],
        secrets: dict[str, str],
        warnings: list[str],
    ) -> list[dict[str, Any]]:
        production = self._get(production_id)
        source = Path((production.get("source_video") or {}).get("path") or "")
        base = str((public.get("video_generator") or {}).get("media_public_base_url") or "")
        video_url, video_warn = providers.public_media_url(
            base,
            production_id,
            source.name if source.name else "source.mp4",
        )

        if video_warn:
            video_url = ""
            uploaded = self._upload_reference_media_to_gcloud(
                production_id,
                public,
                secrets,
                source,
                warnings,
            )
            video_url = uploaded.get("video") or ""

        contents = providers.build_reference_input_contents(video_url)
        logger.info(
            "generate.reference_inputs count=%s urls=%s",
            len(contents),
            ",".join(safe_url(str(item.get("url") or "")) for item in contents) or "-",
        )
        return contents

    def _upload_reference_media_to_gcloud(
        self,
        production_id: str,
        public: dict[str, Any],
        secrets: dict[str, str],
        source: Path,
        warnings: list[str],
    ) -> dict[str, str]:
        from .understand.gateway import GeminiGatewayClient

        mm = public.get("multimodal") or {}
        upload_url = str(mm.get("upload_url") or "").strip()
        if not upload_url:
            message = (
                "未配置 media_public_base_url 或 GCloud 上传地址，"
                "参考视频无法提供给 Seedance"
            )
            warnings.append(message)
            self._emit(production_id, "warning", {"stage": "generate", "message": message})
            return {}

        client = GeminiGatewayClient(
            base_url=str(mm.get("base_url") or ""),
            upload_url=upload_url,
            token_url=str(mm.get("token_url") or ""),
            api_key=secrets.get("multimodal") or "",
            model=str(mm.get("model") or ""),
            source=str(mm.get("source") or ""),
            timeout_seconds=int(mm.get("timeout_seconds") or 120),
        )
        logger.info(
            "generate.reference_upload_started endpoint=%s production_id=%s",
            safe_url(upload_url),
            production_id,
        )
        try:
            token = client.get_access_token()
        except Exception as exc:  # noqa: BLE001
            message = f"GCloud 参考视频上传凭证获取失败：{clip_text(str(exc), 200)}"
            warnings.append(message)
            self._emit(
                production_id,
                "warning",
                {
                    "stage": "generate",
                    "step": "reference_upload",
                    "message": message,
                    "details": {"error_type": exc.__class__.__name__},
                },
            )
            logger.warning("generate.reference_upload_auth_failed", exc_info=True)
            return {}

        media: list[tuple[str, Path]] = [("video", source)]
        uploaded: dict[str, str] = {}
        failed: list[str] = []
        for role, path in media:
            if not path.is_file():
                failed.append(f"{role}:file_missing")
                continue
            object_name = f"{production_id}-{path.name}"
            try:
                url = client.upload_media(
                    path,
                    token,
                    object_name=object_name,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "generate.reference_upload_failed role=%s file=%s",
                    role,
                    file_summary(path),
                    exc_info=True,
                )
                failed.append(f"{role}:{exc.__class__.__name__}")
                continue
            if url:
                uploaded[role] = url
            else:
                failed.append(f"{role}:empty_url")

        if failed:
            message = (
                "GCloud 参考视频上传未全部成功（"
                + ", ".join(failed)
                + "），Seedance 将仅收到已上传成功的参考素材"
            )
            warnings.append(message)
            self._emit(
                production_id,
                "warning",
                {"stage": "generate", "step": "reference_upload", "message": message},
            )
        logger.info(
            "generate.reference_upload_completed uploaded=%s failed=%s",
            ",".join(sorted(uploaded)) or "-",
            len(failed),
        )
        return uploaded

    def _get(self, production_id: str) -> dict[str, Any]:
        production = self.db.get_production(production_id)
        if not production:
            raise ValueError("创作任务不存在")
        return production

    def _emit(self, production_id: str, kind: str, payload: dict[str, Any]) -> None:
        self.db.append_production_event(production_id, kind, payload)
        callback = getattr(self, "on_after_emit", None)
        if callback:
            try:
                callback(production_id, kind, payload)
            except Exception:
                logger.exception("conversation card sync failed for %s", production_id)

    def _raise_if_cancelled(self, cancel: threading.Event) -> None:
        if cancel.is_set():
            raise PipelineCancelled("任务已取消")


def _is_opening_shot(shot: dict[str, Any], script: dict[str, Any] | None = None) -> bool:
    if shot.get("source_kind") == "opening":
        return True
    shot_id = str((script or {}).get("opening", {}).get("shot_id") or "shot_opening")
    return str(shot.get("id") or "") == shot_id


def _opening_from(production: dict[str, Any], script: dict[str, Any] | None = None) -> dict[str, Any]:
    opening = dict((production.get("decomposition") or {}).get("opening") or {})
    if script and script.get("opening"):
        opening = {**opening, **(script.get("opening") or {})}
    return opening


def _reuse_range(
    shot: dict[str, Any],
    segments: dict[str, Any],
    production: dict[str, Any],
) -> tuple[int, int, dict[str, Any] | None]:
    opening = _opening_from(production, production.get("script") or {})
    if _is_opening_shot(shot, production.get("script") or {}) and int(opening.get("end_ms") or 0) > 0:
        return 0, int(opening["end_ms"]), segments.get(shot.get("source_segment_id") or "") or None
    segment = segments.get(shot.get("source_segment_id") or "")
    if not segment:
        return 0, 0, None
    return int(segment["start_ms"]), int(segment["end_ms"]), segment


def shot_edge_fades(index: int, shots: list[dict[str, Any]], opening_shot_id: str | None = None) -> tuple[bool, bool]:
    """开头块内部不打淡化；只允许开头结束与后续镜头交界、以及后续镜头之间淡化。"""
    if index < 0 or index >= len(shots):
        return False, False
    action = (shots[index].get("action") if index < len(shots) else "") or ""
    prev_action = (shots[index - 1].get("action") if index > 0 else "") or ""
    next_action = (shots[index + 1].get("action") if index + 1 < len(shots) else "") or ""
    fade_in = bool(prev_action and prev_action != action)
    fade_out = bool(next_action and next_action != action)
    current_id = str(shots[index].get("id") or "")
    is_opening = current_id == (opening_shot_id or "") or shots[index].get("source_kind") == "opening"
    if is_opening:
        fade_in = False
    return fade_in, fade_out




def plan_segments(
    duration_ms: int,
    cuts: list[int],
    silence: list[int],
    has_audio: bool,
) -> list[dict[str, Any]]:
    duration_ms = max(int(duration_ms), 1)
    window = min(3000, max(1500, int(duration_ms * 0.15)))
    hook_target = window
    cta_target = max(duration_ms - window, window)
    min_gap_ms = 900
    max_mid_segments = 8

    def snap(target: int, low: int, high: int, invent: bool) -> int | None:
        if has_audio and silence:
            nearby = [point for point in silence if low <= point <= high and abs(point - target) <= 200]
            if nearby:
                return min(nearby, key=lambda point: abs(point - target))
        nearby = [point for point in cuts if low <= point <= high]
        if nearby:
            return min(nearby, key=lambda point: abs(point - target))
        if invent:
            return min(max(target, low), high)
        return None

    hook_high = max(duration_ms - 1200, 801)
    hook_end = snap(hook_target, 800, hook_high, invent=False)
    if hook_end is None:
        later = [
            point
            for point in list(cuts) + (list(silence) if has_audio else [])
            if 800 <= point <= duration_ms - 400
        ]
        hook_end = min(later) if later else None
    hook_for_cta = hook_end if hook_end is not None else 0
    cta_start = snap(cta_target, hook_for_cta + 400, max(duration_ms - 400, hook_for_cta + 400), invent=True)
    assert cta_start is not None
    mid_start = hook_end if hook_end is not None else 400
    mid_end = cta_start - 400
    mid_span = max(mid_end - mid_start, 0)
    desired_mid = min(max_mid_segments, max(0, int(round(mid_span / 9000))))
    if mid_span >= 6500:
        desired_mid = max(1, desired_mid)

    def select_mid_cuts() -> list[int]:
        if desired_mid <= 0 or mid_end <= mid_start:
            return []
        candidates = sorted(
            {
                int(cut)
                for cut in cuts
                if mid_start + 300 <= int(cut) <= mid_end - 300
            }
        )
        targets = [
            int(round(mid_start + mid_span * index / (desired_mid + 1)))
            for index in range(1, desired_mid + 1)
        ]
        selected: list[int] = []
        for target in targets:
            pool = [item for item in candidates if item not in selected]
            if not pool:
                chosen = target
            else:
                chosen = min(pool, key=lambda item: abs(item - target))
            if not selected or abs(chosen - selected[-1]) >= min_gap_ms:
                selected.append(chosen)
        return sorted(selected)

    if hook_end is not None:
        mid_cuts = select_mid_cuts()
        bounds = [0, hook_end, *mid_cuts, cta_start, duration_ms]
    else:
        mid_cuts = select_mid_cuts()
        bounds = [0, *mid_cuts, cta_start, duration_ms]
    unique: list[int] = []
    for value in sorted(bounds):
        value = min(max(value, 0), duration_ms)
        if not unique or value - unique[-1] >= min_gap_ms:
            unique.append(value)
    if unique[-1] != duration_ms:
        if duration_ms - unique[-1] <= 100:
            unique[-1] = duration_ms
        else:
            unique.append(duration_ms)
    if unique[0] != 0:
        unique = [0, *unique]
    segments: list[dict[str, Any]] = []
    for index, (start, end) in enumerate(zip(unique, unique[1:], strict=False), start=1):
        if index == 1:
            role = "hook"
        elif end >= duration_ms and start >= cta_start - 50:
            role = "cta"
        else:
            role = "value"
        segments.append(
            {
                "id": f"seg_{index:02d}",
                "role": role,
                "start_ms": start,
                "end_ms": end,
            }
        )
    if segments and segments[-1]["role"] != "cta":
        segments[-1]["role"] = "cta"
    return segments


def brief_one_liner(brief: str) -> str:
    text = re.sub(r"\s+", " ", (brief or "").strip()) or "补一条可投放的短视频"
    sentence = re.split(r"[。！？!?；;\n]", text, maxsplit=1)[0].strip() or text
    return sentence[:80]


def local_intent(
    brief: str,
    decomposition: dict[str, Any],
    refs: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    text = brief.strip() or "补一条可投放的短视频"
    hook_seg = next((seg for seg in decomposition.get("segments") or [] if seg.get("role") == "hook"), None)
    value_seg = next((seg for seg in decomposition.get("segments") or [] if seg.get("role") == "value"), None)
    cta_seg = next((seg for seg in decomposition.get("segments") or [] if seg.get("role") == "cta"), None)
    intent = {
        "goal": f"让观众理解并记住：{text[:80]}",
        "audience": "会被该商品或主题吸引的短视频观众",
        "promise": text[:120],
        "assumptions": ["未提供的商品细节不虚构", "参考库只用于结构启发"],
        "references": [{"path": item["path"], "why": item["why"]} for item in refs],
    }
    skeleton = {
        "one_liner": brief_one_liner(text),
        "hook": (hook_seg or {}).get("summary") or f"开场抓住注意：{text[:40]}",
        "value": (value_seg or {}).get("summary") or "展示核心证据或卖点",
        "cta": (cta_seg or {}).get("summary") or "收束行动号召",
    }
    return intent, skeleton



_DURATION_PHRASE = re.compile(r"[，,、]?\s*时长\s*\d+\s*ms。?", re.I)
_TEMPLATE_COPY = re.compile(
    r"^(HOOK|VALUE|CTA|OTHER)\s*段$|按时间窗口划分|场景切点划分",
    re.I,
)
_GENERIC_ON_SCREEN = {"hook", "value", "cta", "other", "shot"}


def strip_duration_phrase(text: str) -> str:
    cleaned = _DURATION_PHRASE.sub("", text or "")
    return re.sub(r"\s+", " ", cleaned).strip(" 。，,;；")


def _is_template_copy(text: str) -> bool:
    cleaned = strip_duration_phrase(text)
    if not cleaned:
        return True
    return bool(_TEMPLATE_COPY.search(cleaned))


_ROLE_MARK = re.compile(
    r"(开场|开头|hook|中间|价值段|商品展示|value|结尾|cta|行动号召)",
    re.I,
)
_TRAILING_WRAPPER = re.compile(r"(的短视频|的视频|短视频|视频)\s*$")
_ROLE_FALLBACK = {
    "hook": "开场抓住注意",
    "value": "展示核心卖点",
    "cta": "立即下单",
}


def _mark_role(mark: str) -> str:
    token = (mark or "").strip().lower()
    if token in {"开场", "开头", "hook"}:
        return "hook"
    if token in {"结尾", "cta", "行动号召"}:
        return "cta"
    return "value"


def brief_role_phrases(brief: str) -> dict[str, list[str]]:
    """Split a creative brief on 开场/中间/结尾 so each role gets its own clause."""
    text = strip_rewrite_instructions(brief)
    found: dict[str, list[str]] = {"hook": [], "value": [], "cta": []}
    if not text:
        return found
    marks = list(_ROLE_MARK.finditer(text))
    if not marks:
        return found
    for index, match in enumerate(marks):
        role = _mark_role(match.group(1))
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        chunk = _TRAILING_WRAPPER.sub("", text[match.start():end])
        chunk = strip_duration_phrase(chunk).strip(" ，,、。；;：:的")
        if chunk and chunk not in found[role]:
            found[role].append(chunk)
    return found


def _brief_role_line(brief: str, role: str, value_index: int = 0) -> str:
    phrases = brief_role_phrases(brief)
    if role == "value":
        pool = phrases.get("value") or []
        if value_index < len(pool):
            return pool[value_index]
        if value_index > 0:
            return "补充卖点"
        if pool:
            return pool[0]
        return _ROLE_FALLBACK["value"]
    pool = phrases.get(role) or []
    if pool:
        return pool[0]
    creative = strip_rewrite_instructions(brief)
    # A hook may state the topic once; do not stamp the same first sentence on every shot.
    if role == "hook":
        one = brief_one_liner(creative) if creative else ""
        return one or _ROLE_FALLBACK["hook"]
    return _ROLE_FALLBACK.get(role) or _ROLE_FALLBACK["value"]


def shot_voiceover(
    segment: dict[str, Any],
    skeleton: dict[str, Any],
    duration_ms: int,
    brief: str = "",
    value_index: int = 0,
) -> str:
    role = segment.get("role") or "other"
    if role == "other":
        return ""
    summary = strip_duration_phrase(str(segment.get("summary") or ""))
    if _is_template_copy(summary):
        summary = ""
    skeleton_line = strip_duration_phrase(str((skeleton or {}).get(role) or ""))
    if _is_template_copy(skeleton_line):
        skeleton_line = ""
    # Prefer this segment's own useful summary; never reuse another value shot's copy.
    base = summary or skeleton_line or _brief_role_line(brief, role, value_index)
    base = strip_duration_phrase(base)
    # Rewrite instructions ("把第二段和结尾重新生成") must not become burned copy.
    if not strip_rewrite_instructions(base):
        base = _brief_role_line(brief, role, value_index)
    return strip_duration_phrase(base)


def shot_on_screen_text(segment: dict[str, Any], voiceover: str) -> str:
    raw = strip_duration_phrase(str(segment.get("on_screen_text") or ""))
    if raw and raw.strip().lower() not in _GENERIC_ON_SCREEN:
        return raw[:24]
    return (voiceover or "")[:24]


def local_script(production: dict[str, Any]) -> dict[str, Any]:
    brief = production.get("brief") or ""
    segments = (production.get("decomposition") or {}).get("segments") or []
    opening = dict((production.get("decomposition") or {}).get("opening") or {})
    skeleton = production.get("skeleton") or {}
    plan = production.get("replace_plan") or {}
    generate_all = bool(re.search(r"全部新生成|全部生成|all generate|generate all", brief, re.I))
    reuse_most = bool(re.search(r"尽量复用|复用原片|reuse", brief, re.I))
    width = int((production.get("source_video") or {}).get("width") or 1080)
    height = int((production.get("source_video") or {}).get("height") or 1920)
    aspect = "9:16" if height > width else "16:9"
    opening_end = int(opening.get("end_ms") or 0)
    opening_ids = set(opening.get("segment_ids") or [])
    if opening_end > 0 and not opening_ids:
        opening_ids = {str(seg.get("id") or "") for seg in segments if int(seg.get("start_ms") or 0) < opening_end}

    def segment_action(segment: dict[str, Any], in_opening: bool) -> str:
        if in_opening:
            if generate_all:
                return "generate"
            if plan:
                return "generate" if opening_block_generate(plan, opening, generate_all) else "reuse"
            return "reuse"
        role = segment.get("role") or "other"
        if plan:
            return "generate" if segment.get("id") in set(plan.get("resolved_segment_ids") or []) else "reuse"
        if generate_all:
            return "generate"
        if reuse_most:
            return "reuse" if role == "value" else "generate"
        return "generate" if role in {"hook", "cta"} else "reuse"

    def build_shot(
        index: int,
        segment: dict[str, Any],
        action: str,
        duration_ms: int | None = None,
        shot_id: str | None = None,
        source_kind: str = "",
        value_index: int = 0,
    ) -> dict[str, Any]:
        role = segment.get("role") or "other"
        default_duration = int(segment["end_ms"]) - int(segment["start_ms"])
        duration_ms = max(int(duration_ms if duration_ms is not None else default_duration), 1)
        visual = segment.get("visual") or f"{role} 镜头"
        text = shot_voiceover(segment, skeleton, duration_ms, brief=brief, value_index=value_index)
        prompt = (
            f"{visual}. {text}. Slow camera move, {duration_ms}ms, {width}x{height} {aspect}, "
            "natural lighting, no logos, product-focused."
        )
        item = {
            "id": shot_id or f"shot_{index:02d}",
            "role": role if role in {"hook", "value", "cta", "other"} else "other",
            "duration_ms": duration_ms,
            "action": action,
            "source_segment_id": segment.get("id") or "",
            "visual": visual,
            "voiceover": text if role != "other" else "",
            "on_screen_text": shot_on_screen_text(segment, text) if role != "other" else "",
            "prompt": prompt if action == "generate" else "",
        }
        if source_kind:
            item["source_kind"] = source_kind
        return item

    shots: list[dict[str, Any]] = []
    value_cursor = 0
    if opening_end > 0:
        in_opening = [
            seg
            for seg in segments
            if str(seg.get("id") or "") in opening_ids or int(seg.get("start_ms") or 0) < opening_end
        ]
        after = [seg for seg in segments if int(seg.get("start_ms") or 0) >= opening_end]
        head = in_opening[0] if in_opening else {
            "id": "",
            "role": "hook",
            "start_ms": 0,
            "end_ms": opening_end,
            "summary": "",
            "visual": "开头连续镜头",
            "on_screen_text": "",
        }
        # Keep hook copy as voiceover lead, then append later opening summaries.
        parts: list[str] = []
        for item in in_opening or [head]:
            role = item.get("role") or "other"
            value_index = value_cursor
            if role == "value":
                value_cursor += 1
            span = max(int(item.get("end_ms") or 0) - int(item.get("start_ms") or 0), 1)
            line = shot_voiceover(item, skeleton, span, brief=brief, value_index=value_index)
            if line and line not in parts:
                parts.append(line)
        head = {
            **head,
            "visual": "开头连续镜头",
            "summary": " ".join(parts) or (head.get("summary") or "开场连续块"),
        }
        action = segment_action(head, True)
        opening_shot = build_shot(
            1,
            head,
            action,
            duration_ms=opening_end,
            shot_id="shot_opening",
            source_kind="opening",
        )
        opening_shot["role"] = "hook"
        opening_shot["voiceover"] = " ".join(parts) or opening_shot.get("voiceover") or ""
        opening_shot["on_screen_text"] = shot_on_screen_text(head, opening_shot["voiceover"])
        if action == "generate" and not opening_shot.get("prompt"):
            opening_shot["prompt"] = opening_shot["visual"]
        shots.append(opening_shot)
        for offset, segment in enumerate(after, start=2):
            role = segment.get("role") or "other"
            value_index = value_cursor
            if role == "value":
                value_cursor += 1
            shots.append(build_shot(offset, segment, segment_action(segment, False), value_index=value_index))
    else:
        for index, segment in enumerate(segments, start=1):
            role = segment.get("role") or "other"
            value_index = value_cursor
            if role == "value":
                value_cursor += 1
            shots.append(build_shot(index, segment, segment_action(segment, False), value_index=value_index))
    total = sum(int(shot["duration_ms"]) for shot in shots)
    script = {
        "total_duration_ms": total,
        "shots": shots,
        "audio_plan": {
            "voiceover_language": "zh",
            "bgm": "model_generated",
            "subtitles": False,
        },
    }
    if opening_end > 0:
        script["opening"] = {
            "start_ms": 0,
            "end_ms": opening_end,
            "shot_id": "shot_opening",
        }
    return script

def normalize_script(data: dict[str, Any], production: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    shots_in = data.get("shots") or fallback.get("shots") or []
    if not shots_in:
        return fallback
    segments = {seg["id"]: seg for seg in (production.get("decomposition") or {}).get("segments") or []}
    shots: list[dict[str, Any]] = []
    for index, raw in enumerate(shots_in, start=1):
        action = raw.get("action") if raw.get("action") in {"reuse", "generate"} else "generate"
        source_id = raw.get("source_segment_id") if raw.get("source_segment_id") in segments else None
        if action == "reuse" and not source_id:
            action = "generate"
        duration_ms = int(raw.get("duration_ms") or 2000)
        prompt = str(raw.get("prompt") or "")
        if action == "generate" and not prompt:
            prompt = str(raw.get("visual") or production.get("brief") or "Generate a short product shot")
        item = {
            "id": str(raw.get("id") or f"shot_{index:02d}"),
            "role": raw.get("role") if raw.get("role") in {"hook", "value", "cta", "other"} else "other",
            "duration_ms": duration_ms,
            "action": action,
            "source_segment_id": source_id or next(iter(segments), ""),
            "visual": str(raw.get("visual") or ""),
            "voiceover": str(raw.get("voiceover") or ""),
            "on_screen_text": str(raw.get("on_screen_text") or ""),
            "prompt": prompt if action == "generate" else "",
        }
        if raw.get("source_kind"):
            item["source_kind"] = raw.get("source_kind")
        shots.append(item)
    audio = data.get("audio_plan") or fallback.get("audio_plan") or {}
    script = {
        "total_duration_ms": int(data.get("total_duration_ms") or sum(s["duration_ms"] for s in shots)),
        "shots": shots,
        "audio_plan": {
            "voiceover_language": audio.get("voiceover_language") or "zh",
            "bgm": "model_generated",
            "subtitles": False,
        },
    }
    if data.get("opening"):
        script["opening"] = data.get("opening")
    return merge_opening_script(script, production)


def merge_opening_script(script: dict[str, Any], production: dict[str, Any]) -> dict[str, Any]:
    opening = dict((production.get("decomposition") or {}).get("opening") or {})
    end_ms = int(opening.get("end_ms") or 0)
    if end_ms <= 0:
        return script
    shots = list(script.get("shots") or [])
    already = [shot for shot in shots if _is_opening_shot(shot, script) or shot.get("id") == "shot_opening"]
    if already:
        already[0]["id"] = "shot_opening"
        already[0]["source_kind"] = "opening"
        already[0]["duration_ms"] = end_ms
        if opening_block_generate(production.get("replace_plan") or {}, opening):
            already[0]["action"] = "generate"
            if not already[0].get("prompt"):
                already[0]["prompt"] = already[0].get("visual") or production.get("brief") or "Generate opening block"
        script = {
            **script,
            "opening": {"start_ms": 0, "end_ms": end_ms, "shot_id": "shot_opening"},
            "shots": shots,
            "total_duration_ms": sum(int(item["duration_ms"]) for item in shots),
        }
        return script
    cursor = 0
    in_shots: list[dict[str, Any]] = []
    after: list[dict[str, Any]] = []
    for shot in shots:
        start = cursor
        cursor += int(shot.get("duration_ms") or 0)
        if start < end_ms:
            in_shots.append(shot)
        else:
            after.append(shot)
    if not in_shots:
        return {
            **script,
            "opening": {"start_ms": 0, "end_ms": end_ms, "shot_id": "shot_opening"},
        }
    head = dict(in_shots[0])
    head["id"] = "shot_opening"
    head["source_kind"] = "opening"
    head["role"] = "hook"
    head["duration_ms"] = end_ms
    voices = [
        str(item.get("voiceover") or "").strip()
        for item in in_shots
        if str(item.get("voiceover") or "").strip()
    ]
    screens = [
        str(item.get("on_screen_text") or "").strip()
        for item in in_shots
        if str(item.get("on_screen_text") or "").strip()
    ]
    if voices:
        head["voiceover"] = " ".join(voices)
    if screens:
        head["on_screen_text"] = screens[0]
    head["visual"] = head.get("visual") or "开头连续镜头"
    plan = production.get("replace_plan") or {}
    generate_all = bool(
        re.search(
            r"全部新生成|全部生成|all generate|generate all",
            str(production.get("brief") or ""),
            re.I,
        )
    )
    if generate_all or opening_block_generate(plan, opening, generate_all):
        head["action"] = "generate"
        if not head.get("prompt"):
            head["prompt"] = head.get("visual") or production.get("brief") or "Generate opening block"
    else:
        head["action"] = "reuse"
        head["prompt"] = ""
    merged = [head, *after]
    return {
        **script,
        "opening": {"start_ms": 0, "end_ms": end_ms, "shot_id": "shot_opening"},
        "shots": merged,
        "total_duration_ms": sum(int(item["duration_ms"]) for item in merged),
    }


def normalize_model_segments(
    incoming: list[dict[str, Any]],
    duration_ms: int,
    fallback: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not incoming:
        return fallback
    cleaned: list[dict[str, Any]] = []
    for index, raw in enumerate(incoming, start=1):
        start = max(int(raw.get("start_ms") or 0), 0)
        end = min(int(raw.get("end_ms") or start + 1), duration_ms)
        if end <= start:
            continue
        role = raw.get("role") if raw.get("role") in {"hook", "value", "cta", "other"} else "value"
        cleaned.append(
            {
                "id": str(raw.get("id") or f"seg_{index:02d}"),
                "role": role,
                "start_ms": start,
                "end_ms": end,
                "summary": str(raw.get("summary") or fallback[min(index - 1, len(fallback) - 1)]["summary"]),
                "visual": str(raw.get("visual") or ""),
                "audio": str(raw.get("audio") or ""),
                "on_screen_text": str(raw.get("on_screen_text") or role.upper()),
                "reuse_recommended": (
                    bool(raw.get("reuse_recommended"))
                    if "reuse_recommended" in raw
                    else role == "value"
                ),
            }
        )
    if not cleaned:
        return fallback
    cleaned.sort(key=lambda item: item["start_ms"])
    # resolve overlaps and coverage
    resolved: list[dict[str, Any]] = []
    cursor = 0
    for item in cleaned:
        start = max(item["start_ms"], cursor)
        end = max(item["end_ms"], start + 1)
        item = {**item, "start_ms": start, "end_ms": min(end, duration_ms)}
        if item["end_ms"] > item["start_ms"]:
            resolved.append(item)
            cursor = item["end_ms"]
    if resolved and resolved[0]["start_ms"] != 0:
        resolved[0]["start_ms"] = 0
    if resolved and abs(resolved[-1]["end_ms"] - duration_ms) <= 100:
        resolved[-1]["end_ms"] = duration_ms
    elif resolved and resolved[-1]["end_ms"] < duration_ms:
        resolved[-1]["end_ms"] = duration_ms
    roles = {item["role"] for item in resolved}
    if "hook" not in roles:
        resolved[0]["role"] = "hook"
    if "cta" not in roles:
        resolved[-1]["role"] = "cta"
    return resolved


def production_summary_fallback(segments: list[dict[str, Any]], duration_ms: int) -> str:
    return f"参考视频 {duration_ms}ms，共 {len(segments)} 段。"


def _has_audio(path: Path) -> bool:
    try:
        return bool(media.probe(path).get("has_audio"))
    except Exception:
        return False
