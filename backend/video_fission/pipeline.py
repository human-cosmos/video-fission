from __future__ import annotations

import logging
import re
import threading
from pathlib import Path
from typing import Any

from . import media, providers
from .config import Settings
from .db import Database
from .paths import production_artifact_dir
from .references import ReferenceLibrary
from .replace import apply_replace_plan, resolve_replace_plan, strip_rewrite_instructions
from .settings_store import SettingsStore

logger = logging.getLogger(__name__)

STAGES = ("ingest", "decompose", "intent", "script", "generate", "compose")


class PipelineCancelled(RuntimeError):
    pass


class PipelineEngine:
    def __init__(self, settings: Settings, db: Database, store: SettingsStore):
        self.settings = settings
        self.db = db
        self.store = store
        self.library = ReferenceLibrary(settings.project_root)

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
        cancel = cancel or threading.Event()
        production = self.db.get_production(production_id)
        if not production:
            raise ValueError("创作任务不存在")
        stages = list(STAGES)
        if resume_from:
            if resume_from not in STAGES:
                raise ValueError("resume_from 只能是 ingest|decompose|intent")
            if resume_from not in {"ingest", "decompose", "intent"}:
                raise ValueError("resume_from 只能是 ingest|decompose|intent")
            if resume_from == "intent" and not (production.get("decomposition") or {}).get("segments"):
                raise ValueError("还没有拆解结果，无法从 intent 恢复")
            stages = list(STAGES[STAGES.index(resume_from):])
        warnings: list[str] = list(production.get("warnings") or [])
        try:
            for stage in stages:
                self._raise_if_cancelled(cancel)
                self._emit(production_id, "stage_started", {"stage": stage})
                self.db.update_production(production_id, {"current_stage": stage, "status": "running"})
                handler = getattr(self, f"_stage_{stage}")
                handler(production_id, cancel, warnings)
                self._emit(production_id, "stage_completed", {"stage": stage})
            self.db.update_production(
                production_id,
                {"status": "completed", "current_stage": "done", "error": None, "warnings": warnings},
            )
            self._emit(production_id, "completed", {"stage": "done"})
        except PipelineCancelled:
            self.db.update_production(production_id, {"status": "cancelled", "warnings": warnings})
            self._emit(production_id, "error", {"stage": "cancelled", "message": "已取消"})
        except Exception as exc:
            logger.exception("production %s failed", production_id)
            message = str(exc) or exc.__class__.__name__
            self.db.update_production(
                production_id,
                {"status": "failed", "error": message, "warnings": warnings},
            )
            self._emit(production_id, "error", {"message": message})
            raise
        result = self.db.get_production(production_id)
        if not result:
            raise RuntimeError("创作任务在完成后丢失")
        return result

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
        production = self._get(production_id)
        source = production["source_video"]
        src = Path(source["path"])
        info = media.probe(src)
        duration_ms = info["duration_ms"]
        cuts = media.scene_cut_ms(src)
        silence = media.silence_points_ms(src) if info["has_audio"] else []
        segments = plan_segments(duration_ms, cuts, silence, info["has_audio"])
        art = self.artifact_dir(production_id)
        for segment in segments:
            span = max(segment["end_ms"] - segment["start_ms"], 1)
            stamps = [
                segment["start_ms"] + int(span * ratio)
                for ratio in (0.15, 0.5, 0.82)
            ]
            stamps = [
                min(max(ts, segment["start_ms"]), max(segment["end_ms"] - 40, segment["start_ms"]))
                for ts in stamps
            ]
            frames = media.extract_frames(src, stamps, art, f"{segment['id']}_frame")
            segment["frames"] = [self.artifact_url(production_id, path.name) for path in frames]
            segment["visual"] = (
                f"{info['width']}x{info['height']} @{info['fps']:.2f}fps，"
                f"{segment['start_ms']}-{segment['end_ms']}ms，抽取 {len(frames)} 帧"
            )
            segment["summary"] = (
                f"{segment['role'].upper()} 段，时长 {span}ms。"
                f"{'场景切点划分' if cuts else '按时间窗口划分'}。"
            )
            segment["audio"] = "检测到音轨" if info["has_audio"] else "无音轨"
            if not segment.get("on_screen_text"):
                segment["on_screen_text"] = segment["role"].upper()
            self._raise_if_cancelled(cancel)
        decomposition = {
            "summary": (
                f"参考视频 {duration_ms}ms，{info['width']}x{info['height']}，"
                f"共 {len(segments)} 段（Hook / Value / CTA）。"
            ),
            "provider": "local_ffmpeg",
            "model": "",
            "segments": [
                {key: value for key, value in segment.items() if key != "frames"}
                | ({"frames": segment["frames"]} if segment.get("frames") else {})
                for segment in segments
            ],
        }
        # frames is extra; contract doesn't include it. Claude said they'll probe artifact names.
        # Keep contract fields only in stored segments, frames are files on disk.
        clean_segments = []
        for segment in segments:
            clean_segments.append(
                {
                    "id": segment["id"],
                    "role": segment["role"],
                    "start_ms": segment["start_ms"],
                    "end_ms": segment["end_ms"],
                    "summary": segment["summary"],
                    "visual": segment["visual"],
                    "audio": segment["audio"],
                    "on_screen_text": segment["on_screen_text"],
                    "reuse_recommended": segment["role"] == "value",
                }
            )
        decomposition["segments"] = clean_segments

        public, secrets = self._settings_for_production(production_id)
        mm = public["multimodal"]
        if mm.get("provider") == "openai_compatible" and secrets.get("multimodal") and mm.get("base_url"):
            try:
                refined = self._refine_with_multimodal(
                    production, clean_segments, art, mm, secrets["multimodal"]
                )
                if refined:
                    decomposition = refined
            except Exception as exc:
                message = f"多模态拆解失败，已保留本地切分：{exc.__class__.__name__}"
                warnings.append(message)
                self._emit(production_id, "warning", {"stage": "decompose", "message": message})
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
        return {
            "summary": str(data.get("summary") or production_summary_fallback(segments, duration_ms)),
            "provider": "openai_compatible",
            "model": mm.get("model") or "",
            "segments": cleaned,
        }

    def _stage_intent(self, production_id: str, cancel: threading.Event, warnings: list[str]) -> None:
        production = self._get(production_id)
        segments = (production.get("decomposition") or {}).get("segments") or []
        plan = resolve_replace_plan(production.get("replace_policy"), segments, production.get("brief") or "")
        self.db.update_production(production_id, {"replace_plan": plan})
        production = self._get(production_id)
        refs = self.library.retrieve(production["brief"])
        for item in refs:
            path = self.settings.project_root / item["path"]
            if not path.is_file():
                raise ValueError(f"参考文件不存在: {item['path']}")
        intent, skeleton = local_intent(production["brief"], production.get("decomposition") or {}, refs)
        public, secrets = self._settings_for_production(production_id)
        mm = public["multimodal"]
        if mm.get("provider") == "openai_compatible" and secrets.get("multimodal") and mm.get("base_url"):
            try:
                intent, skeleton = self._llm_intent(production, refs, mm, secrets["multimodal"], intent, skeleton)
            except Exception as exc:
                message = f"意图模型失败，已用本地规划器：{exc.__class__.__name__}"
                warnings.append(message)
                self._emit(production_id, "warning", {"stage": "intent", "message": message})
        # Strip excerpts before persisting references (contract only has path/why)
        intent["references"] = [{"path": item["path"], "why": item["why"]} for item in refs]
        self.db.update_production(production_id, {"intent": intent, "skeleton": skeleton, "warnings": warnings})

    def _llm_intent(self, production, refs, mm, api_key, fallback_intent, fallback_skeleton):
        excerpts = []
        for item in refs:
            excerpts.append(
                {
                    "path": item["path"],
                    "skeleton": (item.get("excerpt") or {}).get("skeleton"),
                    "notes": (item.get("excerpt") or {}).get("notes"),
                }
            )
        raw = providers.chat_completions(
            mm.get("base_url") or "",
            api_key,
            mm.get("model") or "",
            [
                {
                    "role": "user",
                    "content": (
                        "按 creative-intent-script-skeleton 产出严格 JSON："
                        '{"intent":{"goal","audience","promise","assumptions":[]},'
                        '"skeleton":{"one_liner","hook","value","cta"}}。'
                        f"创作说明：{production['brief']}\n拆解：{production.get('decomposition')}\n参考：{excerpts}"
                    ),
                }
            ],
            int(mm.get("timeout_seconds") or 120),
        )
        data = providers.extract_json(raw)
        intent = data.get("intent") or {}
        skeleton = data.get("skeleton") or {}
        merged_intent = {
            "goal": str(intent.get("goal") or fallback_intent["goal"]),
            "audience": str(intent.get("audience") or fallback_intent["audience"]),
            "promise": str(intent.get("promise") or fallback_intent["promise"]),
            "assumptions": intent.get("assumptions") or fallback_intent["assumptions"],
            "references": fallback_intent.get("references") or [],
        }
        merged_skeleton = {
            "one_liner": str(skeleton.get("one_liner") or fallback_skeleton["one_liner"]),
            "hook": str(skeleton.get("hook") or fallback_skeleton["hook"]),
            "value": str(skeleton.get("value") or fallback_skeleton["value"]),
            "cta": str(skeleton.get("cta") or fallback_skeleton["cta"]),
        }
        return merged_intent, merged_skeleton

    def _stage_script(self, production_id: str, cancel: threading.Event, warnings: list[str]) -> None:
        production = self._get(production_id)
        refs = (production.get("intent") or {}).get("references") or []
        full_texts: list[str] = []
        for item in refs[: max(3, len(refs))]:
            try:
                full_texts.append(self.library.read_full(item["path"])[:8000])
            except Exception:
                continue
        if len(full_texts) < 3 and refs:
            # Still proceed if library read fewer, but try harder by retrieving again
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
        script = local_script(production)
        public, secrets = self._settings_for_production(production_id)
        mm = public["multimodal"]
        if mm.get("provider") == "openai_compatible" and secrets.get("multimodal") and mm.get("base_url"):
            try:
                script = self._llm_script(production, full_texts, mm, secrets["multimodal"], script)
            except Exception as exc:
                message = f"分镜模型失败，已用本地规划器：{exc.__class__.__name__}"
                warnings.append(message)
                self._emit(production_id, "warning", {"stage": "script", "message": message})
        script = apply_replace_plan(script, production)
        self.db.update_production(production_id, {"script": script, "warnings": warnings})

    def _llm_script(self, production, full_texts, mm, api_key, fallback):
        raw = providers.chat_completions(
            mm.get("base_url") or "",
            api_key,
            mm.get("model") or "",
            [
                {
                    "role": "user",
                    "content": (
                        "按 universal-ugc-video-script-generation 产出严格 JSON script 对象，"
                        "字段为 total_duration_ms, shots[], audio_plan。"
                        "shots 需含 id,role,duration_ms,action,source_segment_id,"
                        "visual,voiceover,on_screen_text,prompt。"
                        "action 只能是 reuse 或 generate。reuse 必须带 source_segment_id。"
                        f"创作说明：{production['brief']}\n骨架：{production.get('skeleton')}\n"
                        f"拆解：{production.get('decomposition')}\n参考全文节选：{full_texts[:3]}"
                    ),
                }
            ],
            int(mm.get("timeout_seconds") or 120),
        )
        data = providers.extract_json(raw)
        return normalize_script(data, production, fallback)

    def _stage_generate(self, production_id: str, cancel: threading.Event, warnings: list[str]) -> None:
        production = self._get(production_id)
        source = production["source_video"]
        script = production.get("script") or {}
        segments = {seg["id"]: seg for seg in (production.get("decomposition") or {}).get("segments") or []}
        width, height = media.target_dimensions(int(source["width"]), int(source["height"]))
        fps = float(source["fps"] or 30)
        src = Path(source["path"])
        art = self.artifact_dir(production_id)
        public, secrets = self._settings_for_production(production_id)
        vg = public["video_generator"]
        clips: list[dict[str, Any]] = []
        for shot in script.get("shots") or []:
            self._raise_if_cancelled(cancel)
            dest = art / f"{shot['id']}.mp4"
            duration_ms = int(shot["duration_ms"])
            if shot["action"] == "reuse":
                segment = segments.get(shot.get("source_segment_id") or "")
                if not segment:
                    raise ValueError(f"{shot['id']} 缺少可复用源片段")
                media.cut_segment(src, dest, int(segment["start_ms"]), int(segment["end_ms"]), width, height, fps, True)
                if not _has_audio(dest):
                    media.add_silence_audio(dest, dest.with_name(dest.stem + "_a.mp4"), duration_ms)
                    dest.with_name(dest.stem + "_a.mp4").replace(dest)
                provider_name = "local_ffmpeg"
            else:
                provider_name = vg.get("provider") or "local_ffmpeg"
                generated = False
                if provider_name in {"openai_compatible", "generic_http"} and vg.get("base_url"):
                    try:
                        providers.generate_remote_video(
                            provider_name,
                            vg.get("base_url") or "",
                            secrets.get("video_generator") or "",
                            vg.get("model") or "",
                            shot.get("prompt") or shot.get("visual") or production["brief"],
                            duration_ms,
                            width,
                            height,
                            fps,
                            int(vg.get("timeout_seconds") or 300),
                            dest,
                        )
                        generated = dest.is_file() and dest.stat().st_size > 0
                    except Exception as exc:
                        if vg.get("fallback_to_local", True):
                            message = f"{shot['id']} 远端生成失败，已回退本地：{exc.__class__.__name__}"
                            warnings.append(message)
                            self._emit(production_id, "warning", {"stage": "generate", "message": message})
                            provider_name = "local_ffmpeg"
                        else:
                            raise
                if not generated:
                    frame = _shot_frame(art, shot, segments)
                    burn_in = bool(public.get("compose", {}).get("burn_in_subtitles", True))
                    text = shot.get("on_screen_text") or ""
                    media.ken_burns_clip(
                        frame,
                        dest,
                        duration_ms,
                        width,
                        height,
                        fps,
                        text,
                        overlay_text=not burn_in,
                    )
                    provider_name = "local_ffmpeg"
                if not _has_audio(dest):
                    tmp = dest.with_name(dest.stem + "_a.mp4")
                    segment = segments.get(shot.get("source_segment_id") or "")
                    if segment and media.probe(src).get("has_audio"):
                        media.mux_timeline_audio(
                            dest,
                            src,
                            int(segment["start_ms"]),
                            tmp,
                            duration_ms,
                        )
                    else:
                        media.add_silence_audio(dest, tmp, duration_ms)
                    tmp.replace(dest)
            info = media.probe(dest)
            if abs(info["duration_ms"] - duration_ms) > 100 and info["duration_ms"] <= 0:
                raise RuntimeError(f"{shot['id']} 生成结果无法探测")
            if dest.stat().st_size < 1000:
                raise RuntimeError(f"{shot['id']} 生成了空文件")
            clips.append(
                {
                    "shot_id": shot["id"],
                    "path": str(dest),
                    "url": self.artifact_url(production_id, dest.name),
                    "provider": provider_name,
                    "duration_ms": info["duration_ms"],
                }
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
        public, _secrets = self._settings_for_production(production_id)
        compose = public["compose"]
        shots = script.get("shots") or []
        source_info = production.get("source_video") or {}
        fps = float(source_info.get("fps") or 30)
        faded: list[Path] = []
        for index, item in enumerate(clips):
            path = Path(item["path"])
            action = (shots[index]["action"] if index < len(shots) else "") or ""
            prev_action = (shots[index - 1]["action"] if index > 0 and index - 1 < len(shots) else "")
            next_action = (shots[index + 1]["action"] if index + 1 < len(shots) else "")
            fade_in = bool(prev_action and prev_action != action)
            fade_out = bool(next_action and next_action != action)
            duration_ms = int((shots[index]["duration_ms"] if index < len(shots) else item.get("duration_ms")) or 1)
            if fade_in or fade_out:
                faded_path = art / f"{item['shot_id']}_fade.mp4"
                media.apply_edge_fades(path, faded_path, duration_ms, fps, fade_in, fade_out)
                faded.append(faded_path)
            else:
                faded.append(path)
        concat_path = art / "concat.mp4"
        media.concat_clips(faded, concat_path)
        current = concat_path
        source = Path(source_info["path"])
        total_ms = int(script.get("total_duration_ms") or source_info["duration_ms"])
        if compose.get("mix_reference_bgm", True):
            try:
                bgm = art / "reference-audio.m4a"
                media.extract_audio(source, bgm)
                mixed = art / "with-bgm.mp4"
                media.mix_bgm(current, bgm, mixed, float(compose.get("bgm_volume") or 0.18), total_ms)
                current = mixed
            except Exception as exc:
                message = f"参考 BGM 混合失败，已保留片段音轨：{exc.__class__.__name__}"
                warnings.append(message)
                self._emit(production_id, "warning", {"stage": "compose", "message": message})
        srt_path = art / "final.srt"
        write_srt(srt_path, shots)
        burned = art / "burned.mp4"
        final_path = art / "final.mp4"
        if compose.get("burn_in_subtitles", True):
            try:
                media.burn_subtitles(current, srt_path, burned)
                current = burned
            except Exception as exc:
                message = f"字幕硬烧失败，已输出无硬字幕成片：{exc.__class__.__name__}"
                warnings.append(message)
                self._emit(production_id, "warning", {"stage": "compose", "message": message})
        try:
            media.fit_av_duration(current, final_path, total_ms, fps)
        except Exception as exc:
            warnings.append(f"音画对齐失败，已保留当前成片：{exc.__class__.__name__}")
            self._emit(production_id, "warning", {"stage": "compose", "message": warnings[-1]})
            if current != final_path:
                final_path.write_bytes(current.read_bytes())
        info = media.probe(final_path)
        if abs(info["duration_ms"] - total_ms) > 500:
            warnings.append(
                f"成片时长 {info['duration_ms']}ms 与脚本 {total_ms}ms 偏差超过 0.5s"
            )
        output = {
            "video_path": str(final_path),
            "video_url": self.artifact_url(production_id, "final.mp4"),
            "srt_path": str(srt_path),
            "srt_url": self.artifact_url(production_id, "final.srt"),
        }
        self.db.update_production(production_id, {"output": output, "warnings": warnings})

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

    def snap(target: int, low: int, high: int) -> int:
        if has_audio and silence:
            nearby = [point for point in silence if low <= point <= high and abs(point - target) <= 200]
            if nearby:
                return min(nearby, key=lambda point: abs(point - target))
        # Prefer a real scene cut anywhere in the legal hook/cta window,
        # not only one within 400ms of the nominal 15% target.
        nearby = [point for point in cuts if low <= point <= high]
        if nearby:
            return min(nearby, key=lambda point: abs(point - target))
        return min(max(target, low), high)

    hook_end = snap(hook_target, 800, max(duration_ms - 1200, 801))
    cta_start = snap(cta_target, hook_end + 400, max(duration_ms - 400, hook_end + 400))
    mid_cuts = [cut for cut in cuts if hook_end + 400 < cut < cta_start - 400]
    if len(cuts) < 2:
        mid_cuts = []
        cursor = hook_end + 4000
        while cursor < cta_start - 800:
            mid_cuts.append(cursor)
            cursor += 4000
    bounds = [0, hook_end, *mid_cuts, cta_start, duration_ms]
    unique: list[int] = []
    for value in sorted(bounds):
        value = min(max(value, 0), duration_ms)
        if not unique or value - unique[-1] >= 200:
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
    skeleton = production.get("skeleton") or {}
    plan = production.get("replace_plan") or {}
    resolved = set(plan.get("resolved_segment_ids") or [])
    generate_all = bool(re.search(r"全部新生成|全部生成|all generate|generate all", brief, re.I))
    reuse_most = bool(re.search(r"尽量复用|复用原片|reuse", brief, re.I))
    shots: list[dict[str, Any]] = []
    value_cursor = 0
    width = int((production.get("source_video") or {}).get("width") or 1080)
    height = int((production.get("source_video") or {}).get("height") or 1920)
    aspect = "9:16" if height > width else "16:9"
    for index, segment in enumerate(segments, start=1):
        role = segment.get("role") or "other"
        duration_ms = max(int(segment["end_ms"]) - int(segment["start_ms"]), 1)
        if plan:
            action = "generate" if segment.get("id") in resolved else "reuse"
        elif generate_all:
            action = "generate"
        elif reuse_most:
            action = "reuse" if role == "value" else "generate"
        else:
            action = "generate" if role in {"hook", "cta"} else "reuse"
        if action == "reuse":
            source_id = segment["id"]
        else:
            source_id = segment["id"]
        visual = segment.get("visual") or f"{role} 镜头"
        value_index = value_cursor
        if role == "value":
            value_cursor += 1
        text = shot_voiceover(segment, skeleton, duration_ms, brief=brief, value_index=value_index)
        prompt = (
            f"{visual}. {text}. Slow camera move, {duration_ms}ms, {width}x{height} {aspect}, "
            "natural lighting, no logos, product-focused."
        )
        shots.append(
            {
                "id": f"shot_{index:02d}",
                "role": role if role in {"hook", "value", "cta", "other"} else "other",
                "duration_ms": duration_ms,
                "action": action,
                "source_segment_id": source_id if action == "reuse" or True else None,
                "visual": visual,
                "voiceover": text if role != "other" else "",
                "on_screen_text": shot_on_screen_text(segment, text) if role != "other" else "",
                "prompt": prompt if action == "generate" else "",
            }
        )
        if action == "reuse":
            shots[-1]["source_segment_id"] = segment["id"]
        # generate shots still record the related segment for keyframes
        if action == "generate":
            shots[-1]["source_segment_id"] = segment["id"]
            if not shots[-1]["prompt"]:
                shots[-1]["prompt"] = prompt
    # Contract: reuse must have source_segment_id; generate must have prompt.
    total = sum(int(shot["duration_ms"]) for shot in shots)
    return {
        "total_duration_ms": total,
        "shots": shots,
        "audio_plan": {
            "voiceover_language": "zh",
            "bgm": "reuse_reference",
            "subtitles": True,
        },
    }


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
        shots.append(
            {
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
        )
    audio = data.get("audio_plan") or fallback.get("audio_plan") or {}
    return {
        "total_duration_ms": int(data.get("total_duration_ms") or sum(s["duration_ms"] for s in shots)),
        "shots": shots,
        "audio_plan": {
            "voiceover_language": audio.get("voiceover_language") or "zh",
            "bgm": audio.get("bgm") or "reuse_reference",
            "subtitles": bool(audio["subtitles"]) if "subtitles" in audio else True,
        },
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


def write_srt(path: Path, shots: list[dict[str, Any]]) -> None:
    cursor = 0
    blocks: list[str] = []
    index = 1
    for shot in shots:
        duration = int(shot.get("duration_ms") or 0)
        text = strip_duration_phrase(str(shot.get("voiceover") or shot.get("on_screen_text") or ""))
        if text:
            blocks.append(f"{index}\n{_srt_ts(cursor)} --> {_srt_ts(cursor + duration)}\n{text}\n")
            index += 1
        cursor += duration
    path.write_text("\n".join(blocks) + ("\n" if blocks else ""), encoding="utf-8")


def _srt_ts(ms: int) -> str:
    ms = max(int(ms), 0)
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _shot_frame(art: Path, shot: dict[str, Any], segments: dict[str, Any]) -> Path:
    segment_id = shot.get("source_segment_id") or ""
    # Prefer the mid-segment frame so Ken Burns does not sit on a cut
    # (frame_01 is taken at 15% and can still show the previous shot).
    for name in (
        f"{segment_id}_frame_02.jpg",
        f"{segment_id}_frame_01.jpg",
        f"{segment_id}_frame_03.jpg",
    ):
        path = art / name
        if path.is_file():
            return path
    named = sorted(art.glob(f"{segment_id}_frame_*.jpg"))
    if named:
        return named[0]
    frames = sorted(art.glob("*_frame_*.jpg"))
    if frames:
        return frames[0]
    raise ValueError(f"找不到 {shot.get('id')} 的关键帧")


def _has_audio(path: Path) -> bool:
    try:
        return bool(media.probe(path).get("has_audio"))
    except Exception:
        return False
