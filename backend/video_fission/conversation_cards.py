from __future__ import annotations

from pathlib import Path
from typing import Any

VIDEO_CARD_TYPES = (
    "video_progress",
    "video_understanding",
    "video_plan",
    "video_segments",
    "video_observe",
    "video_output",
)


def stable_item_id(production_id: str, item_type: str) -> str:
    suffix = item_type.removeprefix("video_")
    return f"{production_id}:{suffix}"


def upload_media_url(upload_id: str | None) -> str:
    if not upload_id:
        return ""
    return f"/api/productions/uploads/{upload_id}"


def artifact_url(production_id: str, name: str) -> str:
    return f"/api/productions/{production_id}/artifacts/{name}"


def _upload_id(public: dict[str, Any]) -> str:
    source = public.get("source_video") or {}
    return str(source.get("upload_id") or public.get("video_upload_id") or "")


def progress_payload(public: dict[str, Any]) -> dict[str, Any]:
    status = public.get("status") or "draft"
    return {
        "production_id": public["id"],
        "title": public.get("title") or "",
        "brief": public.get("brief") or "",
        "status": status,
        "current_stage": public.get("current_stage") or "",
        "warnings": public.get("warnings") or [],
        "error": public.get("error"),
        "cancellable": status == "running",
    }


def understanding_payload(public: dict[str, Any]) -> dict[str, Any] | None:
    decomposition = public.get("decomposition")
    if not isinstance(decomposition, dict) or not decomposition:
        return None
    source = dict(public.get("source_video") or {})
    source_url = upload_media_url(_upload_id(public))
    if source_url:
        source["url"] = source_url
    tags = decomposition.get("video_tags") if isinstance(decomposition.get("video_tags"), dict) else {}
    return {
        "production_id": public["id"],
        "summary": decomposition.get("summary") or "",
        "description": decomposition.get("description") or tags.get("video_describe") or "",
        "provider": decomposition.get("provider") or "",
        "model": decomposition.get("model") or "",
        "video_tags": tags,
        "segments": list(decomposition.get("segments") or []),
        "source_video": source,
        "source_video_url": source_url,
    }


def plan_payload(public: dict[str, Any]) -> dict[str, Any] | None:
    if not any(public.get(key) for key in ("intent", "skeleton", "script", "replace_plan")):
        return None
    return {
        "production_id": public["id"],
        "intent": public.get("intent"),
        "skeleton": public.get("skeleton"),
        "script": public.get("script"),
        "replace_policy": public.get("replace_policy"),
        "replace_plan": public.get("replace_plan"),
    }


def _segment_action(segment_id: str, public: dict[str, Any]) -> str:
    clips = public.get("clips") or []
    for clip in clips:
        source = str(clip.get("source_segment_id") or clip.get("id") or "")
        if source == segment_id or clip.get("id") == segment_id:
            return str(clip.get("action") or "")
    plan = public.get("replace_plan") or {}
    resolved = set(plan.get("resolved_segment_ids") or [])
    if resolved:
        return "generate" if segment_id in resolved else "reuse"
    return ""


def _preview_url(production_id: str, segment_id: str, artifact_dir: Path | None) -> str:
    if not segment_id:
        return ""
    candidates = [f"{segment_id}_frame_02.jpg", f"{segment_id}_frame_01.jpg", f"{segment_id}_frame_03.jpg"]
    if artifact_dir is not None:
        for name in candidates:
            if (artifact_dir / name).is_file():
                return artifact_url(production_id, name)
        return ""
    return artifact_url(production_id, candidates[0])


def segments_payload(public: dict[str, Any], artifact_dir: Path | None = None) -> dict[str, Any] | None:
    decomposition = public.get("decomposition")
    if not isinstance(decomposition, dict) or not decomposition:
        return None
    production_id = public["id"]
    items = []
    for segment in decomposition.get("segments") or []:
        segment_id = str(segment.get("id") or "")
        items.append(
            {
                **segment,
                "action": _segment_action(segment_id, public),
                "preview_url": _preview_url(production_id, segment_id, artifact_dir),
            }
        )
    clips = []
    for clip in public.get("clips") or []:
        name = Path(str(clip.get("path") or "")).name
        preview = artifact_url(production_id, name) if name else ""
        clips.append({**clip, "preview_url": preview or clip.get("preview_url") or ""})
    return {
        "production_id": production_id,
        "status": public.get("status"),
        "current_stage": public.get("current_stage") or "",
        "updated_at": public.get("updated_at"),
        "segments": items,
        "clips": clips,
        "replace_plan": public.get("replace_plan"),
        "replace_policy": public.get("replace_policy"),
    }


def output_payload(public: dict[str, Any]) -> dict[str, Any] | None:
    output = public.get("output")
    status = public.get("status")
    if not output and status not in {"completed", "failed"}:
        return None
    return {
        "production_id": public["id"],
        "status": status,
        "error": public.get("error"),
        "output": output,
        "video_url": (output or {}).get("video_url") if isinstance(output, dict) else "",
        "srt_url": (output or {}).get("srt_url") if isinstance(output, dict) else "",
        "updated_at": public.get("updated_at"),
    }


def _observe_empty(observe: Any) -> bool:
    if not isinstance(observe, dict):
        return True
    if observe.get("shot_observation_card") or observe.get("raw_claims"):
        return False
    speech = observe.get("speech_evidence") or {}
    if isinstance(speech, dict) and (
        speech.get("segments") or str(speech.get("status") or "not_used") not in {"", "not_used"}
    ):
        return False
    return True


def observe_payload(public: dict[str, Any]) -> dict[str, Any] | None:
    decomposition = public.get("decomposition")
    if not isinstance(decomposition, dict) or not decomposition:
        return None
    items = []
    for segment in decomposition.get("segments") or []:
        observe = segment.get("observe")
        error = segment.get("error")
        if _observe_empty(observe) and not error:
            continue
        if _observe_empty(observe) and error and "observe" not in str(error):
            # 切片失败但没有观察结果时不单独占观察卡；仅 observe 失败才列出
            continue
        items.append(
            {
                "id": segment.get("id"),
                "role": segment.get("role"),
                "start_ms": segment.get("start_ms"),
                "end_ms": segment.get("end_ms"),
                "summary": segment.get("summary") or "",
                "observe": observe if isinstance(observe, dict) else {},
                "error": error,
            }
        )
    if not items:
        return None
    return {
        "production_id": public["id"],
        "status": public.get("status"),
        "updated_at": public.get("updated_at"),
        "segments": items,
    }


def cards_for(public: dict[str, Any], artifact_dir: Path | None = None) -> list[tuple[str, dict[str, Any]]]:
    cards: list[tuple[str, dict[str, Any]]] = [("video_progress", progress_payload(public))]
    understanding = understanding_payload(public)
    if understanding:
        cards.append(("video_understanding", understanding))
    plan = plan_payload(public)
    if plan:
        cards.append(("video_plan", plan))
    segments = segments_payload(public, artifact_dir)
    if segments:
        cards.append(("video_segments", segments))
    observe = observe_payload(public)
    if observe:
        cards.append(("video_observe", observe))
    output = output_payload(public)
    if output:
        cards.append(("video_output", output))
    return cards
