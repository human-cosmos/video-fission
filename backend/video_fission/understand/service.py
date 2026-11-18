"""decompose 增强：转场检测、整片理解、切片、片段解析与 observe。"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from pathlib import Path
from typing import Any

from video_fission import media, providers
from video_fission.logging_utils import clip_text, file_summary, safe_url

from .clips import build_lens_prompt, cut_playback_clip, empty_lens_info, ensure_under_size, normalize_clip_result
from .gateway import GeminiGatewayClient, video_mime
from .normalize import (
    build_split_prompt,
    coverage_ok,
    coverage_ok_lenses,
    empty_video_tags,
    ensure_segment_summaries,
    extract_json,
    extract_video_tags,
    normalize_segments,
    observe_is_empty,
    raw_segments,
    to_production_segments,
    validate_segments,
)
from .observe import empty_observe, extract_observe_payload, normalize_observe_response, observe_prompt, probe_has_audio
from .opening import estimate_local_opening, finalize_opening, parse_opening, snap_opening_end
from .records import GeminiCallRecorder
from .transitions import TransitionDetector, TransitionInfo, detection_available

logger = logging.getLogger(__name__)

Emit = Callable[[str, dict[str, Any]], None]


def _cancel_requested(cancel: Any) -> bool:
    return cancel is not None and bool(getattr(cancel, "is_set", lambda: False)())


def _raise_if_cancelled(cancel: Any) -> None:
    if _cancel_requested(cancel):
        from video_fission.pipeline import PipelineCancelled

        raise PipelineCancelled("任务已取消")


def _run_cancellable(work: Callable[[], Any], cancel: Any) -> Any:
    """Run blocking model I/O without making cancellation wait for its timeout."""
    _raise_if_cancelled(cancel)
    results: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def run() -> None:
        try:
            results.put((True, work()))
        except BaseException as exc:  # noqa: BLE001
            results.put((False, exc))

    context = copy_context()
    threading.Thread(target=context.run, args=(run,), name="video-understand-request", daemon=True).start()
    while True:
        _raise_if_cancelled(cancel)
        try:
            ok, value = results.get(timeout=0.2)
        except queue.Empty:
            continue
        if ok:
            return value
        raise value


def _record_gemini_call(
    recorder: GeminiCallRecorder | None,
    *,
    label: str,
    input_mode: str,
    started: float,
    response: Any = None,
    error: BaseException | None = None,
) -> None:
    if recorder is None:
        return
    try:
        recorder.record(
            label=label,
            input_mode=input_mode,
            status="failed" if error is not None else "succeeded",
            elapsed_ms=(time.perf_counter() - started) * 1000,
            response=response,
            error=error,
        )
    except Exception:  # noqa: BLE001 - diagnostics must not break video processing
        logger.warning(
            "gemini_api_result_save_failed type=%s label=%s",
            recorder.call_type,
            label,
            exc_info=True,
        )


def _call_gemini_recorded(
    client: GeminiGatewayClient,
    recorder: GeminiCallRecorder | None,
    *,
    label: str,
    input_mode: str,
    call: Callable[[], Any],
    cancel: Any,
) -> Any:
    started = time.perf_counter()
    try:
        response = _run_cancellable(call, cancel)
    except Exception as exc:
        _record_gemini_call(
            recorder,
            label=label,
            input_mode=input_mode,
            started=started,
            error=exc,
        )
        raise
    _record_gemini_call(
        recorder,
        label=label,
        input_mode=input_mode,
        started=started,
        response=response,
    )
    return response


def understand_settings(public: dict[str, Any]) -> dict[str, Any]:
    section = public.get("understand") or {}
    return {
        "transition_detection": bool(section.get("transition_detection", True)),
        "clip_analysis": bool(section.get("clip_analysis", True)),
        "observe": bool(section.get("observe", True)),
        "clip_workers": max(1, min(int(section.get("clip_workers") or 3), 8)),
        "gemini_max_size_mb": max(1.0, float(section.get("gemini_max_size_mb") or 15)),
        "observe_timeout_seconds": max(30, min(int(section.get("observe_timeout_seconds") or 360), 3600)),
    }


def run_decompose(
    production: dict[str, Any],
    artifact_dir: Path,
    public: dict[str, Any],
    secrets: dict[str, str],
    warnings: list[str],
    emit: Emit | None = None,
    cancel: Any = None,
) -> dict[str, Any]:
    from video_fission.pipeline import plan_segments

    production_id = production["id"]
    source = production["source_video"]
    src = Path(source["path"])
    info = media.probe(src)
    duration_ms = int(info["duration_ms"])
    duration_s = duration_ms / 1000
    options = understand_settings(public)
    mm = public.get("multimodal") or {}
    provider = str(mm.get("provider") or "local_ffmpeg")
    remote_ready = bool(
        provider in {"openai_compatible", "gemini_gateway"}
        and bool(mm.get("base_url"))
        and (bool(secrets.get("multimodal")) or (provider == "gemini_gateway" and bool(mm.get("token_url"))))
    )
    logger.info(
        "decompose.started file=%s duration_ms=%s resolution=%sx%s fps=%.3f "
        "provider=%s model=%s remote_ready=%s options=%s",
        file_summary(src),
        duration_ms,
        info["width"],
        info["height"],
        float(info["fps"] or 0),
        provider,
        mm.get("model") or "-",
        remote_ready,
        options,
    )

    def _emit(kind: str, payload: dict[str, Any]) -> None:
        if emit:
            emit(kind, payload)

    def _check() -> None:
        _raise_if_cancelled(cancel)

    transition_info = TransitionInfo(ok=False)
    cuts: list[int] = []
    _emit("stage_started", {"stage": "decompose", "step": "transition"})
    transition_started = time.perf_counter()
    if options["transition_detection"]:
        if detection_available():
            try:
                transition_info = TransitionDetector().analyze(src)
                logger.info(
                    "decompose.transition_model_completed ok=%s fps=%.3f total_frames=%s "
                    "frame_scores=%s cnn_scores=%s warning=%s",
                    transition_info.ok,
                    transition_info.fps,
                    transition_info.total_frames,
                    len(transition_info.frame_scores),
                    len(transition_info.cnn_scores),
                    clip_text(transition_info.warning, 200),
                )
                if transition_info.warning:
                    warnings.append(transition_info.warning)
                    _emit("warning", {"stage": "decompose", "step": "transition", "message": transition_info.warning})
                if transition_info.ok:
                    cuts = TransitionDetector().cuts_ms(transition_info)
            except Exception as exc:  # noqa: BLE001
                message = f"转场检测失败，已退化为 ffmpeg 场景切分：{exc.__class__.__name__}"
                warnings.append(message)
                logger.warning("decompose.transition_failed fallback=ffmpeg_scene", exc_info=True)
                _emit("warning", {"stage": "decompose", "step": "transition", "message": message})
        else:
            message = "转场检测依赖未安装，已退化为 ffmpeg 场景切分"
            warnings.append(message)
            logger.warning("decompose.transition_unavailable fallback=ffmpeg_scene")
            _emit("warning", {"stage": "decompose", "step": "transition", "message": message})
    if not cuts:
        cuts = media.scene_cut_ms(src)
    logger.info(
        "decompose.transition_completed elapsed_ms=%.1f cuts=%s preview=%s",
        (time.perf_counter() - transition_started) * 1000,
        len(cuts),
        cuts[:20],
    )
    _emit(
        "stage_completed",
        {
            "stage": "decompose",
            "step": "transition",
            "message": f"识别到 {len(cuts)} 个转场切点",
            "details": {
                "cuts": len(cuts),
                "resolution": f"{info['width']} × {info['height']}",
                "fps": f"{float(info.get('fps') or 0):.2f}",
            },
        },
    )
    _check()

    silence = media.silence_points_ms(src) if info["has_audio"] else []
    local_plan = plan_segments(duration_ms, cuts, silence, info["has_audio"])
    logger.info(
        "decompose.local_plan segments=%s silence_points=%s ranges=%s",
        len(local_plan),
        len(silence),
        [(item["role"], item["start_ms"], item["end_ms"]) for item in local_plan],
    )
    local_lenses = [
        {
            "id": item["id"],
            "role": item["role"],
            "start_time": item["start_ms"] / 1000,
            "end_time": item["end_ms"] / 1000,
            "summary": "",
            "description": "",
            "slot": [],
            "sub_slot": [],
            "sub_slot_desc": [],
        }
        for item in local_plan
    ]

    video_tags = empty_video_tags()
    used_provider = "local_ffmpeg"
    model_name = ""
    remote_lenses: list[dict[str, Any]] | None = None
    model_opening: dict[str, Any] | None = None

    _emit("stage_started", {"stage": "decompose", "step": "understand"})
    understand_started = time.perf_counter()
    if remote_ready:
        try:
            remote_lenses, video_tags, used_provider, model_name, remote_warning, model_opening = _remote_split(
                src,
                artifact_dir,
                duration_s,
                mm,
                secrets.get("multimodal") or "",
                provider,
                options,
                transition_info,
                cancel,
            )
            if remote_warning:
                warnings.append(remote_warning)
                logger.warning("decompose.understand_warning message=%s", remote_warning)
                _emit("warning", {"stage": "decompose", "step": "understand", "message": remote_warning})
            if remote_lenses is not None and model_opening is None:
                message = "模型未给出有效开头区间，已改用本地暂估，不能当作模型开头"
                warnings.append(message)
                logger.warning("decompose.opening_missing fallback=local")
                _emit("warning", {"stage": "decompose", "step": "understand", "message": message})
        except Exception as exc:  # noqa: BLE001
            from video_fission.pipeline import PipelineCancelled

            if isinstance(exc, PipelineCancelled):
                raise
            message = f"整片理解失败，已保留本地切分：{exc.__class__.__name__}"
            warnings.append(message)
            logger.warning("decompose.understand_failed fallback=local_plan", exc_info=True)
            _emit("warning", {"stage": "decompose", "step": "understand", "message": message})
            remote_lenses = None
            used_provider = "local_ffmpeg"
            video_tags = empty_video_tags()
            model_opening = None
    logger.info(
        "decompose.understand_completed elapsed_ms=%.1f provider=%s segments=%s tags=%s opening=%s",
        (time.perf_counter() - understand_started) * 1000,
        used_provider,
        len(remote_lenses or []),
        len(video_tags),
        len(str((model_opening or {}).get("reason") or "")),
    )
    understood_segments = len(remote_lenses or [])
    tag_count = sum(1 for value in video_tags.values() if value not in (None, "", [], {}))
    _emit(
        "stage_completed",
        {
            "stage": "decompose",
            "step": "understand",
            "message": (
                f"整片理解完成，得到 {understood_segments} 个镜头"
                if understood_segments
                else "整片理解完成，已保留本地切分"
            ),
            "details": {
                "segments": understood_segments,
                "tags": tag_count,
                "provider": used_provider,
                "model": model_name or "",
            },
        },
    )
    _check()

    if remote_lenses:
        _emit("stage_started", {"stage": "decompose", "step": "refine"})
        refine_started = time.perf_counter()
        fps = transition_info.fps or float(info.get("fps") or 30)
        if transition_info.ok:
            try:
                remote_lenses = TransitionDetector().refine_segments(transition_info, remote_lenses)
            except Exception as exc:  # noqa: BLE001
                message = f"转场精修失败，已使用模型时间点：{exc.__class__.__name__}"
                warnings.append(message)
                logger.warning("decompose.refine_failed use_model_timing", exc_info=True)
                _emit("warning", {"stage": "decompose", "step": "refine", "message": message})
        remote_lenses = validate_segments(remote_lenses, duration_s, fps)
        _emit(
            "stage_completed",
            {
                "stage": "decompose",
                "step": "refine",
                "message": f"切片边界校正完成，共 {len(remote_lenses)} 个镜头",
                "details": {"segments": len(remote_lenses)},
            },
        )
        segments = to_production_segments(remote_lenses, duration_ms, production_id)
        logger.info(
            "decompose.refine_completed elapsed_ms=%.1f model_segments=%s production_segments=%s coverage=%s",
            (time.perf_counter() - refine_started) * 1000,
            len(remote_lenses),
            len(segments),
            coverage_ok(segments, duration_ms),
        )
        if not coverage_ok(segments, duration_ms):
            message = "模型切片未覆盖全片，已回退本地切分"
            warnings.append(message)
            logger.warning("decompose.coverage_failed fallback=local_plan")
            _emit("warning", {"stage": "decompose", "step": "refine", "message": message})
            segments = to_production_segments(local_lenses, duration_ms, production_id)
            used_provider = "local_ffmpeg"
            video_tags = empty_video_tags()
            model_opening = None
    else:
        segments = to_production_segments(local_lenses, duration_ms, production_id)

    _fill_local_fields(segments, info, cuts)
    ensure_segment_summaries(segments, fallback=f"参考视频 {duration_ms}ms")

    if model_opening:
        bounds = [int(item["end_ms"]) for item in segments if 0 < int(item["end_ms"]) < duration_ms]
        end_ms = snap_opening_end(
            int(model_opening["end_ms"]),
            duration_ms=duration_ms,
            cuts_ms=cuts,
            silence_ms=silence,
            segment_bounds_ms=bounds,
        )
        opening = {**model_opening, "end_ms": end_ms, "method": "model", "start_ms": 0}
    else:
        opening = estimate_local_opening(duration_ms, cuts, silence)
    opening = finalize_opening(opening, segments, duration_ms, production_id)
    logger.info(
        "decompose.opening_completed method=%s range=0-%sms segment_ids=%s reason=%s",
        opening.get("method") or "-",
        opening.get("end_ms"),
        ",".join(opening.get("segment_ids") or []),
        len(str(opening.get("reason") or "")),
    )

    _emit("stage_started", {"stage": "decompose", "step": "clip_analysis"})
    _cut_and_frame(src, artifact_dir, segments, info, production_id, cancel, _check)
    _cut_opening_clip(src, artifact_dir, opening, info, production_id)
    if remote_ready and used_provider != "local_ffmpeg" and options["clip_analysis"]:
        _analyze_clips(
            segments,
            artifact_dir,
            mm,
            secrets.get("multimodal") or "",
            provider,
            options,
            video_tags,
            warnings,
            _emit,
            cancel,
        )
    _emit(
        "stage_completed",
        {
            "stage": "decompose",
            "step": "clip_analysis",
            "message": f"已提取 {len(segments)} 组片段与关键帧",
            "details": {"segments": len(segments)},
        },
    )
    _check()

    if remote_ready and used_provider != "local_ffmpeg" and options["clip_analysis"] and options["observe"]:
        _emit("stage_started", {"stage": "decompose", "step": "observe"})
        _observe_clips(
            segments,
            artifact_dir,
            mm,
            secrets.get("multimodal") or "",
            provider,
            options,
            warnings,
            _emit,
            cancel,
        )
        observed = sum(
            1 for segment in segments if not observe_is_empty(segment.get("observe"))
        )
        failed = sum(1 for segment in segments if segment.get("error"))
        _emit(
            "stage_completed",
            {
                "stage": "decompose",
                "step": "observe",
                "message": f"片段观察完成，{observed}/{len(segments)} 段有结果",
                "details": {
                    "observed": observed,
                    "failed": failed,
                    "segments": len(segments),
                },
            },
        )
    elif not options["observe"] or provider == "local_ffmpeg" or not remote_ready:
        if options["observe"] and (provider == "local_ffmpeg" or not remote_ready):
            message = "当前无法调用模型，已省略 observe"
            warnings.append(message)
            _emit("warning", {"stage": "decompose", "step": "observe", "message": message})

    summary = str(video_tags.get("video_summary") or "").strip() or (
        f"参考视频 {duration_ms}ms，{info['width']}x{info['height']}，共 {len(segments)} 段（Hook / Value / CTA）。"
    )
    description = str(video_tags.get("video_describe") or "").strip()
    logger.info(
        "decompose.completed provider=%s model=%s segments=%s opening_ms=%s summary=%s",
        used_provider,
        model_name or "-",
        len(segments),
        opening.get("end_ms"),
        len(summary),
    )
    return {
        "summary": summary,
        "description": description,
        "provider": used_provider,
        "model": model_name,
        "video_tags": video_tags,
        "segments": segments,
        "opening": opening,
    }


def _fill_local_fields(segments: list[dict[str, Any]], info: dict[str, Any], cuts: list[int]) -> None:
    cut_note = "场景切点划分" if cuts else "按时间窗口划分"
    for segment in segments:
        span = max(int(segment["end_ms"]) - int(segment["start_ms"]), 1)
        if not segment.get("visual"):
            segment["visual"] = (
                f"{info['width']}x{info['height']} @{info['fps']:.2f}fps，"
                f"{segment['start_ms']}-{segment['end_ms']}ms，抽取 3 帧"
            )
        if not segment.get("audio"):
            segment["audio"] = "检测到音轨" if info["has_audio"] else "无音轨"
        if not segment.get("on_screen_text"):
            segment["on_screen_text"] = str(segment.get("role") or "other").upper()
        if not str(segment.get("summary") or "").strip():
            role = str(segment.get("role") or "other").upper()
            segment["summary"] = f"{role} 段，{cut_note}，{span}ms。"
        if not segment.get("description"):
            segment["description"] = segment["summary"]


def _cut_and_frame(
    src: Path,
    artifact_dir: Path,
    segments: list[dict[str, Any]],
    info: dict[str, Any],
    production_id: str,
    cancel: Any,
    check: Callable[[], None],
) -> None:
    for segment in segments:
        check()
        seg_id = segment["id"]
        dest = artifact_dir / "clips" / f"{seg_id}.mp4"
        started = time.perf_counter()
        try:
            cut_playback_clip(
                src,
                dest,
                int(segment["start_ms"]),
                int(segment["end_ms"]),
                int(info["width"]),
                int(info["height"]),
                float(info["fps"] or 25),
                bool(info["has_audio"]),
            )
            segment["clip_path"] = f"clips/{seg_id}.mp4"
            segment["clip_url"] = f"/api/productions/{production_id}/artifacts/clips/{seg_id}.mp4"
            logger.info(
                "decompose.clip_cut seg_id=%s range=%s-%sms output=%s elapsed_ms=%.1f",
                seg_id,
                segment["start_ms"],
                segment["end_ms"],
                file_summary(dest),
                (time.perf_counter() - started) * 1000,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("decompose.clip_cut_failed seg_id=%s", seg_id, exc_info=True)
            segment["error"] = f"切片失败：{exc.__class__.__name__}"
        span = max(int(segment["end_ms"]) - int(segment["start_ms"]), 1)
        stamps = [int(segment["start_ms"]) + int(span * ratio) for ratio in (0.15, 0.5, 0.82)]
        stamps = [
            min(max(ts, int(segment["start_ms"])), max(int(segment["end_ms"]) - 40, int(segment["start_ms"])))
            for ts in stamps
        ]
        try:
            frames = media.extract_frames(src, stamps, artifact_dir, f"{seg_id}_frame")
            logger.info("decompose.frames_extracted seg_id=%s timestamps=%s frames=%s", seg_id, stamps, len(frames))
        except Exception:  # noqa: BLE001
            logger.warning("decompose.frame_extract_failed seg_id=%s", seg_id, exc_info=True)



def _cut_opening_clip(
    src: Path,
    artifact_dir: Path,
    opening: dict[str, Any],
    info: dict[str, Any],
    production_id: str,
) -> None:
    end_ms = int(opening.get("end_ms") or 0)
    if end_ms <= 0:
        opening.pop("clip_path", None)
        opening.pop("clip_url", None)
        return
    dest = artifact_dir / "clips" / "opening.mp4"
    started = time.perf_counter()
    try:
        cut_playback_clip(
            src,
            dest,
            0,
            end_ms,
            int(info["width"]),
            int(info["height"]),
            float(info["fps"] or 25),
            bool(info["has_audio"]),
        )
        opening["clip_path"] = "clips/opening.mp4"
        opening["clip_url"] = f"/api/productions/{production_id}/artifacts/clips/opening.mp4"
        logger.info(
            "decompose.opening_clip_cut range=0-%sms output=%s elapsed_ms=%.1f",
            end_ms,
            file_summary(dest),
            (time.perf_counter() - started) * 1000,
        )
    except Exception:  # noqa: BLE001
        logger.warning("decompose.opening_clip_cut_failed", exc_info=True)
        opening.pop("clip_path", None)
        opening.pop("clip_url", None)


def _remote_split(
    src: Path,
    artifact_dir: Path,
    duration_s: float,
    mm: dict[str, Any],
    api_key: str,
    provider: str,
    options: dict[str, Any],
    transition_info: TransitionInfo,
    cancel: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any], str, str, str, dict[str, Any] | None]:
    _raise_if_cancelled(cancel)
    started = time.perf_counter()
    recorder = (
        GeminiCallRecorder(
            artifact_dir,
            "video_understanding",
            provider=provider,
            model=str(mm.get("model") or ""),
        )
        if provider == "gemini_gateway"
        else None
    )
    prompt = build_split_prompt(duration_s)
    compressed, warning = ensure_under_size(src, artifact_dir / "tmp", options["gemini_max_size_mb"])
    use_video = compressed.is_file() and compressed.stat().st_size <= options["gemini_max_size_mb"] * 1024 * 1024
    frames = sorted(artifact_dir.glob("seg_*_frame_01.jpg"))[:16]
    if not frames:
        # 还没切段时从整片均匀抽帧
        duration_ms = int(duration_s * 1000)
        stamps = [int(duration_ms * ratio) for ratio in (0.08, 0.2, 0.35, 0.5, 0.65, 0.8, 0.92)]
        frames = media.extract_frames(src, stamps, artifact_dir / "tmp", "understand_frame")
    logger.info(
        "decompose.remote_split_request provider=%s endpoint=%s model=%s use_video=%s "
        "compressed=%s frames=%s prompt_chars=%s",
        provider,
        safe_url(str(mm.get("base_url") or "")),
        mm.get("model") or "-",
        use_video,
        file_summary(compressed),
        len(frames),
        len(prompt),
    )
    data, used_frames = _call_understand(
        provider,
        mm,
        api_key,
        prompt,
        compressed if use_video else None,
        frames,
        temperature=0.6,
        cancel=cancel,
        recorder=recorder,
        label="split",
    )
    _raise_if_cancelled(cancel)
    if used_frames and not warning:
        warning = "模型未能直接接受视频，已改用关键帧"
    if not data:
        raise RuntimeError("模型未返回可用的拆分结果")
    lenses = normalize_segments(raw_segments(data), duration_s)
    if not lenses:
        raise RuntimeError("模型未返回可用的拆分结果")
    if not coverage_ok_lenses(lenses, duration_s):
        raise RuntimeError("模型切片未覆盖全片或存在明显空隙")
    tags = extract_video_tags(data)
    opening = parse_opening(data, int(duration_s * 1000))
    logger.info(
        "decompose.remote_split_completed provider=%s segments=%s tags=%s opening=%s used_frames=%s elapsed_ms=%.1f",
        provider,
        len(lenses),
        len(tags),
        opening.get("end_ms") if opening else "-",
        used_frames,
        (time.perf_counter() - started) * 1000,
    )
    return lenses, tags, provider, str(mm.get("model") or ""), warning, opening


def _call_understand(
    provider: str,
    mm: dict[str, Any],
    api_key: str,
    prompt: str,
    video_path: Path | None,
    frames: list[Path],
    temperature: float,
    timeout_seconds: int | None = None,
    max_tokens: int | None = None,
    cancel: Any = None,
    recorder: GeminiCallRecorder | None = None,
    label: str = "understand",
) -> tuple[Any, bool]:
    timeout = int(timeout_seconds or mm.get("timeout_seconds") or 120)
    output_tokens = int(max_tokens or mm.get("max_tokens") or 12000)
    logger.info(
        "llm.understand_call_started provider=%s endpoint=%s model=%s video=%s frames=%s "
        "prompt_chars=%s timeout_s=%s max_tokens=%s temperature=%.2f",
        provider,
        safe_url(str(mm.get("base_url") or "")),
        mm.get("model") or "-",
        bool(video_path),
        len(frames),
        len(prompt),
        timeout,
        output_tokens,
        temperature,
    )
    if provider == "gemini_gateway":
        client = GeminiGatewayClient(
            base_url=str(mm.get("base_url") or ""),
            upload_url=str(mm.get("upload_url") or ""),
            token_url=str(mm.get("token_url") or ""),
            api_key=api_key,
            model=str(mm.get("model") or ""),
            source=str(mm.get("source") or ""),
            max_tokens=output_tokens,
            timeout_seconds=timeout,
        )
        if video_path is not None and frames:
            try:
                response = _call_gemini_recorded(
                    client,
                    recorder,
                    label=f"{label}_video",
                    input_mode="video",
                    call=lambda: client.call(prompt, video_path=video_path, temperature=temperature),
                    cancel=cancel,
                )
                payload = client.extract_payload(response)
                if payload is not None:
                    logger.info("llm.understand_call_completed provider=%s input=video", provider)
                    return payload, False
            except Exception:  # noqa: BLE001
                if _cancel_requested(cancel):
                    _raise_if_cancelled(cancel)
                logger.warning("llm.understand_video_failed provider=%s fallback=frames", provider, exc_info=True)
            response = _call_gemini_recorded(
                client,
                recorder,
                label=f"{label}_frames",
                input_mode="frames",
                call=lambda: client.call(prompt, frames=frames, temperature=temperature),
                cancel=cancel,
            )
            logger.info("llm.understand_call_completed provider=%s input=frames", provider)
            return client.extract_payload(response), True
        if video_path is not None:
            response = _call_gemini_recorded(
                client,
                recorder,
                label=f"{label}_video",
                input_mode="video",
                call=lambda: client.call(prompt, video_path=video_path, temperature=temperature),
                cancel=cancel,
            )
            logger.info("llm.understand_call_completed provider=%s input=video", provider)
            return client.extract_payload(response), False
        response = _call_gemini_recorded(
            client,
            recorder,
            label=f"{label}_frames",
            input_mode="frames",
            call=lambda: client.call(prompt, frames=frames, temperature=temperature),
            cancel=cancel,
        )
        logger.info("llm.understand_call_completed provider=%s input=frames", provider)
        return client.extract_payload(response), True

    def _chat(content: list[dict[str, Any]]) -> Any:
        raw = _run_cancellable(
            lambda: providers.chat_completions(
                mm.get("base_url") or "",
                api_key,
                mm.get("model") or "",
                [{"role": "user", "content": content}],
                timeout,
            ),
            cancel,
        )
        logger.info("llm.chat_response_received chars=%s", len(raw))
        return providers.extract_json(raw) or extract_json(raw)

    if video_path is not None:
        try:
            import base64

            data = base64.b64encode(video_path.read_bytes()).decode("ascii")
            content = [
                {"type": "text", "text": prompt},
                {
                    "type": "video_url",
                    "video_url": {"url": f"data:{video_mime(video_path)};base64,{data}"},
                },
            ]
            payload = _chat(content)
            if payload is not None:
                logger.info("llm.understand_call_completed provider=%s input=video", provider)
                return payload, False
        except Exception:
            logger.warning("llm.understand_video_failed provider=%s fallback=frames", provider, exc_info=True)
    content = [{"type": "text", "text": prompt}]
    content.extend(providers.encode_image(path) for path in frames[:16])
    payload = _chat(content)
    logger.info("llm.understand_call_completed provider=%s input=frames", provider)
    return payload, True


def _analyze_clips(
    segments: list[dict[str, Any]],
    artifact_dir: Path,
    mm: dict[str, Any],
    api_key: str,
    provider: str,
    options: dict[str, Any],
    video_tags: dict[str, Any],
    warnings: list[str],
    emit: Emit,
    cancel: Any,
) -> None:
    product = ""
    names = video_tags.get("product_name") or []
    if isinstance(names, list) and names:
        product = str(names[0])
    prompt = build_lens_prompt(product)
    recorder = (
        GeminiCallRecorder(
            artifact_dir,
            "clip_analysis",
            provider=provider,
            model=str(mm.get("model") or ""),
        )
        if provider == "gemini_gateway"
        else None
    )

    def work(segment: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
        started = time.perf_counter()
        clip = artifact_dir / str(segment.get("clip_path") or "")
        frames = sorted(artifact_dir.glob(f"{segment['id']}_frame_*.jpg"))[:8]
        video = clip if clip.is_file() else None
        logger.info(
            "decompose.clip_analysis_started seg_id=%s range=%s-%sms video=%s frames=%s prompt_chars=%s",
            segment["id"],
            segment["start_ms"],
            segment["end_ms"],
            file_summary(video),
            len(frames),
            len(prompt),
        )
        try:
            data, _used = _call_understand(
                provider,
                mm,
                api_key,
                prompt,
                video,
                frames,
                temperature=0.4,
                cancel=cancel,
                recorder=recorder,
                label=f"clip_{segment['id']}",
            )
            if not isinstance(data, dict):
                raise RuntimeError("片段解析不是对象")
            normalized = normalize_clip_result(data)
            logger.info(
                "decompose.clip_analysis_completed seg_id=%s tags=%s product=%s description=%s elapsed_ms=%.1f",
                segment["id"],
                sum(1 for value in normalized.values() if value not in (None, "", [], {})),
                normalized.get("is_include_product"),
                len(str(normalized.get("clip_description") or "")),
                (time.perf_counter() - started) * 1000,
            )
            return segment["id"], normalized, ""
        except Exception as exc:  # noqa: BLE001
            if _cancel_requested(cancel):
                _raise_if_cancelled(cancel)
            logger.warning(
                "decompose.clip_analysis_failed seg_id=%s elapsed_ms=%.1f",
                segment["id"],
                (time.perf_counter() - started) * 1000,
                exc_info=True,
            )
            return segment["id"], empty_lens_info(segment), f"{segment['id']} 片段解析失败：{exc.__class__.__name__}"

    _run_workers(
        segments,
        work,
        options["clip_workers"],
        "lens_info",
        warnings,
        emit,
        "clip_analysis",
        cancel,
    )


def _observe_clips(
    segments: list[dict[str, Any]],
    artifact_dir: Path,
    mm: dict[str, Any],
    api_key: str,
    provider: str,
    options: dict[str, Any],
    warnings: list[str],
    emit: Emit,
    cancel: Any,
) -> None:
    def work(segment: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
        started = time.perf_counter()
        clip = artifact_dir / str(segment.get("clip_path") or "")
        duration_s = max(int(segment["end_ms"]) - int(segment["start_ms"]), 1) / 1000
        has_audio = probe_has_audio(str(clip)) if clip.is_file() else None
        prompt = observe_prompt(duration_s, has_audio, int(segment["start_ms"]) / 1000)
        frames = sorted(artifact_dir.glob(f"{segment['id']}_frame_*.jpg"))[:8]
        video = clip if clip.is_file() else None
        logger.info(
            "decompose.observe_started seg_id=%s range=%s-%sms video=%s frames=%s audio=%s prompt_chars=%s",
            segment["id"],
            segment["start_ms"],
            segment["end_ms"],
            file_summary(video),
            len(frames),
            has_audio,
            len(prompt),
        )
        try:
            data, _used = _call_understand(
                provider,
                mm,
                api_key,
                prompt,
                video,
                frames,
                temperature=0.2,
                timeout_seconds=options["observe_timeout_seconds"],
                cancel=cancel,
            )
            payload = data if isinstance(data, dict) else extract_observe_payload(data or {})
            if not isinstance(payload, dict):
                raise RuntimeError("observe 结果不是对象")
            result = normalize_observe_response(
                payload,
                duration_s,
                has_audio,
                original_start=int(segment["start_ms"]) / 1000,
            )
            if observe_is_empty(result):
                raise RuntimeError("observe 为空")
            logger.info(
                "decompose.observe_completed seg_id=%s elapsed_ms=%.1f result_chars=%s",
                segment["id"],
                (time.perf_counter() - started) * 1000,
                len(str(result)),
            )
            return segment["id"], result, ""
        except Exception as exc:  # noqa: BLE001
            if _cancel_requested(cancel):
                _raise_if_cancelled(cancel)
            logger.warning(
                "decompose.observe_failed seg_id=%s elapsed_ms=%.1f",
                segment["id"],
                (time.perf_counter() - started) * 1000,
                exc_info=True,
            )
            return segment["id"], empty_observe(), f"{segment['id']} observe 失败：{exc.__class__.__name__}"

    results = _run_workers(
        segments,
        work,
        options["clip_workers"],
        "observe",
        warnings,
        emit,
        "observe",
        cancel,
    )
    for segment in segments:
        observe = results.get(segment["id"])
        if observe and not observe_is_empty(observe):
            segment["observe"] = observe
        else:
            segment.pop("observe", None)


def _run_workers(
    segments: list[dict[str, Any]],
    work,
    workers: int,
    field: str,
    warnings: list[str],
    emit: Emit,
    step: str,
    cancel: Any = None,
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    if not segments:
        return results
    started = time.perf_counter()
    logger.info("decompose.workers_started step=%s segments=%s workers=%s", step, len(segments), workers)
    pool = ThreadPoolExecutor(max_workers=max(1, workers))
    futures = {
        pool.submit(copy_context().run, work, segment): segment["id"]
        for segment in segments
    }
    cancelled = False
    try:
        for future in as_completed(futures):
            if _cancel_requested(cancel):
                cancelled = True
                for pending in futures:
                    pending.cancel()
                _raise_if_cancelled(cancel)
            seg_id, payload, error = future.result()
            results[seg_id] = payload
            for segment in segments:
                if segment["id"] == seg_id and field == "lens_info":
                    segment["lens_info"] = payload
            if error:
                logger.warning("decompose.worker_failed step=%s seg_id=%s message=%s", step, seg_id, error)
                warnings.append(error)
                emit("warning", {"stage": "decompose", "step": step, "message": error})
                for segment in segments:
                    if segment["id"] == seg_id and not segment.get("error"):
                        segment["error"] = error
    except Exception:
        if _cancel_requested(cancel):
            cancelled = True
            for pending in futures:
                pending.cancel()
        raise
    finally:
        pool.shutdown(wait=not cancelled, cancel_futures=cancelled)
    logger.info(
        "decompose.workers_completed step=%s results=%s warnings=%s elapsed_ms=%.1f",
        step,
        len(results),
        len(warnings),
        (time.perf_counter() - started) * 1000,
    )
    return results
