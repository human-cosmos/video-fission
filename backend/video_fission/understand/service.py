"""decompose 增强：转场检测、整片理解、切片、片段解析与 observe。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from video_fission import media, providers

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
from .transitions import TransitionDetector, TransitionInfo, detection_available

logger = logging.getLogger(__name__)

Emit = Callable[[str, dict[str, Any]], None]


def _cancel_requested(cancel: Any) -> bool:
    return cancel is not None and bool(getattr(cancel, "is_set", lambda: False)())


def _raise_if_cancelled(cancel: Any) -> None:
    if _cancel_requested(cancel):
        from video_fission.pipeline import PipelineCancelled

        raise PipelineCancelled("任务已取消")


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

    def _emit(kind: str, payload: dict[str, Any]) -> None:
        if emit:
            emit(kind, payload)

    def _check() -> None:
        _raise_if_cancelled(cancel)

    transition_info = TransitionInfo(ok=False)
    cuts: list[int] = []
    _emit("stage_started", {"stage": "decompose", "step": "transition"})
    if options["transition_detection"]:
        if detection_available():
            try:
                transition_info = TransitionDetector().analyze(src)
                if transition_info.warning:
                    warnings.append(transition_info.warning)
                    _emit("warning", {"stage": "decompose", "step": "transition", "message": transition_info.warning})
                if transition_info.ok:
                    cuts = TransitionDetector().cuts_ms(transition_info)
            except Exception as exc:  # noqa: BLE001
                message = f"转场检测失败，已退化为 ffmpeg 场景切分：{exc.__class__.__name__}"
                warnings.append(message)
                _emit("warning", {"stage": "decompose", "step": "transition", "message": message})
        else:
            message = "转场检测依赖未安装，已退化为 ffmpeg 场景切分"
            warnings.append(message)
            _emit("warning", {"stage": "decompose", "step": "transition", "message": message})
    if not cuts:
        cuts = media.scene_cut_ms(src)
    _emit("stage_completed", {"stage": "decompose", "step": "transition"})
    _check()

    silence = media.silence_points_ms(src) if info["has_audio"] else []
    local_plan = plan_segments(duration_ms, cuts, silence, info["has_audio"])
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

    remote_ready = (
        provider in {"openai_compatible", "gemini_gateway"}
        and bool(mm.get("base_url"))
        and (bool(secrets.get("multimodal")) or (provider == "gemini_gateway" and mm.get("token_url")))
    )
    _emit("stage_started", {"stage": "decompose", "step": "understand"})
    if remote_ready:
        try:
            remote_lenses, video_tags, used_provider, model_name, remote_warning = _remote_split(
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
                _emit("warning", {"stage": "decompose", "step": "understand", "message": remote_warning})
        except Exception as exc:  # noqa: BLE001
            from video_fission.pipeline import PipelineCancelled

            if isinstance(exc, PipelineCancelled):
                raise
            message = f"整片理解失败，已保留本地切分：{exc.__class__.__name__}"
            warnings.append(message)
            _emit("warning", {"stage": "decompose", "step": "understand", "message": message})
            remote_lenses = None
            used_provider = "local_ffmpeg"
            video_tags = empty_video_tags()
    _emit("stage_completed", {"stage": "decompose", "step": "understand"})
    _check()

    if remote_lenses:
        _emit("stage_started", {"stage": "decompose", "step": "refine"})
        fps = transition_info.fps or float(info.get("fps") or 30)
        if transition_info.ok:
            try:
                remote_lenses = TransitionDetector().refine_segments(transition_info, remote_lenses)
            except Exception as exc:  # noqa: BLE001
                message = f"转场精修失败，已使用模型时间点：{exc.__class__.__name__}"
                warnings.append(message)
                _emit("warning", {"stage": "decompose", "step": "refine", "message": message})
        remote_lenses = validate_segments(remote_lenses, duration_s, fps)
        _emit("stage_completed", {"stage": "decompose", "step": "refine"})
        segments = to_production_segments(remote_lenses, duration_ms, production_id)
        if not coverage_ok(segments, duration_ms):
            message = "模型切片未覆盖全片，已回退本地切分"
            warnings.append(message)
            _emit("warning", {"stage": "decompose", "step": "refine", "message": message})
            segments = to_production_segments(local_lenses, duration_ms, production_id)
            used_provider = "local_ffmpeg"
            video_tags = empty_video_tags()
    else:
        segments = to_production_segments(local_lenses, duration_ms, production_id)

    _fill_local_fields(segments, info, cuts)
    ensure_segment_summaries(segments, fallback=f"参考视频 {duration_ms}ms")

    _emit("stage_started", {"stage": "decompose", "step": "clip_analysis"})
    _cut_and_frame(src, artifact_dir, segments, info, production_id, cancel, _check)
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
    _emit("stage_completed", {"stage": "decompose", "step": "clip_analysis"})
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
        _emit("stage_completed", {"stage": "decompose", "step": "observe"})
    elif not options["observe"] or provider == "local_ffmpeg" or not remote_ready:
        if options["observe"] and (provider == "local_ffmpeg" or not remote_ready):
            message = "当前无法调用模型，已省略 observe"
            warnings.append(message)
            _emit("warning", {"stage": "decompose", "step": "observe", "message": message})

    summary = str(video_tags.get("video_summary") or "").strip() or (
        f"参考视频 {duration_ms}ms，{info['width']}x{info['height']}，共 {len(segments)} 段（Hook / Value / CTA）。"
    )
    description = str(video_tags.get("video_describe") or "").strip()
    return {
        "summary": summary,
        "description": description,
        "provider": used_provider,
        "model": model_name,
        "video_tags": video_tags,
        "segments": segments,
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
        except Exception as exc:  # noqa: BLE001
            logger.info("切片失败 %s: %s", seg_id, exc)
            segment["error"] = f"切片失败：{exc.__class__.__name__}"
        span = max(int(segment["end_ms"]) - int(segment["start_ms"]), 1)
        stamps = [int(segment["start_ms"]) + int(span * ratio) for ratio in (0.15, 0.5, 0.82)]
        stamps = [
            min(max(ts, int(segment["start_ms"])), max(int(segment["end_ms"]) - 40, int(segment["start_ms"])))
            for ts in stamps
        ]
        try:
            media.extract_frames(src, stamps, artifact_dir, f"{seg_id}_frame")
        except Exception as exc:  # noqa: BLE001
            logger.info("抽帧失败 %s: %s", seg_id, exc)


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
) -> tuple[list[dict[str, Any]], dict[str, Any], str, str, str]:
    _raise_if_cancelled(cancel)
    prompt = build_split_prompt(duration_s)
    compressed, warning = ensure_under_size(src, artifact_dir / "tmp", options["gemini_max_size_mb"])
    use_video = compressed.is_file() and compressed.stat().st_size <= options["gemini_max_size_mb"] * 1024 * 1024
    frames = sorted(artifact_dir.glob("seg_*_frame_01.jpg"))[:16]
    if not frames:
        # 还没切段时从整片均匀抽帧
        duration_ms = int(duration_s * 1000)
        stamps = [int(duration_ms * ratio) for ratio in (0.08, 0.2, 0.35, 0.5, 0.65, 0.8, 0.92)]
        frames = media.extract_frames(src, stamps, artifact_dir / "tmp", "understand_frame")
    data, used_frames = _call_understand(
        provider, mm, api_key, prompt, compressed if use_video else None, frames, temperature=0.6
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
    return lenses, tags, provider, str(mm.get("model") or ""), warning


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
) -> tuple[Any, bool]:
    timeout = int(timeout_seconds or mm.get("timeout_seconds") or 120)
    output_tokens = int(max_tokens or mm.get("max_tokens") or 12000)
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
                response = client.call(prompt, video_path=video_path, temperature=temperature)
                payload = client.extract_payload(response)
                if payload is not None:
                    return payload, False
            except Exception:  # noqa: BLE001
                pass
            response = client.call(prompt, frames=frames, temperature=temperature)
            return client.extract_payload(response), True
        if video_path is not None:
            response = client.call(prompt, video_path=video_path, temperature=temperature)
            return client.extract_payload(response), False
        response = client.call(prompt, frames=frames, temperature=temperature)
        return client.extract_payload(response), True

    def _chat(content: list[dict[str, Any]]) -> Any:
        raw = providers.chat_completions(
            mm.get("base_url") or "",
            api_key,
            mm.get("model") or "",
            [{"role": "user", "content": content}],
            timeout,
        )
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
                return payload, False
        except Exception:
            pass
    content = [{"type": "text", "text": prompt}]
    content.extend(providers.encode_image(path) for path in frames[:16])
    return _chat(content), True


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

    def work(segment: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
        clip = artifact_dir / str(segment.get("clip_path") or "")
        frames = sorted(artifact_dir.glob(f"{segment['id']}_frame_*.jpg"))[:8]
        video = clip if clip.is_file() else None
        try:
            data, _used = _call_understand(provider, mm, api_key, prompt, video, frames, temperature=0.4)
            if not isinstance(data, dict):
                raise RuntimeError("片段解析不是对象")
            return segment["id"], normalize_clip_result(data), ""
        except Exception as exc:  # noqa: BLE001
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
        clip = artifact_dir / str(segment.get("clip_path") or "")
        duration_s = max(int(segment["end_ms"]) - int(segment["start_ms"]), 1) / 1000
        has_audio = probe_has_audio(str(clip)) if clip.is_file() else None
        prompt = observe_prompt(duration_s, has_audio, int(segment["start_ms"]) / 1000)
        frames = sorted(artifact_dir.glob(f"{segment['id']}_frame_*.jpg"))[:8]
        video = clip if clip.is_file() else None
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
            return segment["id"], result, ""
        except Exception as exc:  # noqa: BLE001
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
    pool = ThreadPoolExecutor(max_workers=max(1, workers))
    futures = {pool.submit(work, segment): segment["id"] for segment in segments}
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
    return results
