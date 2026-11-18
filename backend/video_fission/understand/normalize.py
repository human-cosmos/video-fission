"""拆分结果清洗、连续性校验和一级标签到 role 的映射。"""

from __future__ import annotations

import json
import re
from typing import Any

from .prompts import prompt_video_split

_JSON_FENCE = re.compile(r"```(?:json)?", re.IGNORECASE)
_DURATION_TOKEN = "{{VIDEO_DURATION}}"

SLOT_TO_ROLE = {
    "开场钩子": "hook",
    "痛点具象": "value",
    "方案登场": "value",
    "卖点展示": "value",
    "效果展示": "value",
    "场景演示": "value",
    "信任构建": "value",
    "响应号召": "cta",
}

VIDEO_TAG_LIST_FIELDS = (
    "product_name",
    "environment_categories",
    "environment_specific_scenes",
    "model_types",
    "model_social_role",
    "model_appearance_features",
    "style_categories",
    "style_details",
    "product_presentation_types",
    "product_showcase_forms",
    "narration_method_categories",
    "narration_method_details",
    "opening_types",
    "opening_forms",
    "narrative_structures",
    "rhythm_patterns",
    "interaction_modes",
    "BGM_categories",
    "BGM_styles",
)
VIDEO_TAG_TEXT_FIELDS = ("video_describe", "video_summary")


def build_split_prompt(duration_s: float) -> str:
    total = round(max(float(duration_s), 0.0), 1)
    minutes = int(total // 60)
    seconds = total - minutes * 60
    duration_text = f"{total:.1f} 秒（{minutes:02d}:{seconds:04.1f}）"
    return prompt_video_split.replace(_DURATION_TOKEN, duration_text)


def parse_time(value: Any, last: bool = False) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        raise ValueError("时间为空")
    text = text.replace("：", ":")
    for suffix in ("秒", "sec", "s"):
        if text.lower().endswith(suffix):
            text = text[: -len(suffix)].strip()
            break
    if "---" in text:
        parts = [part.strip() for part in text.split("---") if part.strip()]
        if not parts:
            raise ValueError(f"无法解析时间: {value}")
        text = parts[-1] if last else parts[0]
    if ":" not in text:
        return float(text)
    parts = text.split(":")
    try:
        numbers = [float(part) for part in parts]
    except ValueError as exc:
        raise ValueError(f"无法解析时间: {value}") from exc
    seconds = 0.0
    for number in numbers:
        seconds = seconds * 60 + number
    return seconds


def get_lens_data(part: dict[str, Any]) -> tuple[str, str, float, float]:
    description = str(part.get("description") or "").strip()
    summary = str(part.get("summary") or "").strip()
    if "start_time" in part:
        start_time = parse_time(part.get("start_time", 0), last=False)
    else:
        start_time = int(part.get("start_ms") or 0) / 1000
    if "end_time" in part:
        end_time = parse_time(part.get("end_time", 0), last=True)
    else:
        end_time = int(part.get("end_ms") or 0) / 1000
    return description, summary, start_time, end_time


def extract_json(text: str) -> dict[str, Any] | list[Any] | None:
    if not text:
        return None
    cleaned = _JSON_FENCE.sub("", str(text)).strip()
    start = cleaned.find("{")
    array_start = cleaned.find("[")
    if array_start != -1 and (start == -1 or array_start < start):
        start = array_start
    end = cleaned.rfind("}")
    array_end = cleaned.rfind("]")
    if array_end > end:
        end = array_end
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(cleaned[start : end + 1])
        return data if isinstance(data, (dict, list)) else None
    except json.JSONDecodeError:
        return None


def normalize_tag_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[，,、;；]", value)
    elif not isinstance(value, (list, tuple)):
        value = []
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def normalize_tags(part: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    level1 = normalize_tag_list(part.get("tag_level1") or part.get("slot", []))
    level2 = normalize_tag_list(part.get("tag_level2") or part.get("sub_slot", []))
    level3 = normalize_tag_list(part.get("tag_level3") or part.get("sub_slot_desc", []))
    return level1, level2, level3[:3]


def role_from_slot(slot: list[str] | str | None) -> str:
    tags = normalize_tag_list(slot)
    if not tags:
        return "other"
    for tag in tags:
        if tag in SLOT_TO_ROLE:
            return SLOT_TO_ROLE[tag]
    return "other"


def empty_video_tags() -> dict[str, Any]:
    tags: dict[str, Any] = {key: [] for key in VIDEO_TAG_LIST_FIELDS}
    for key in VIDEO_TAG_TEXT_FIELDS:
        tags[key] = ""
    return tags


def _video_tag_source(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    if "video_lens" in data or any(key in data for key in VIDEO_TAG_LIST_FIELDS):
        return data
    for key in ("data", "result", "content", "text", "message", "output", "answer"):
        nested = data.get(key)
        if isinstance(nested, str):
            nested = extract_json(nested)
        if isinstance(nested, dict):
            source = _video_tag_source(nested)
            if source:
                return source
    return {}


def extract_video_tags(data: Any) -> dict[str, Any]:
    source = _video_tag_source(data)
    tags: dict[str, Any] = {key: normalize_tag_list(source.get(key, [])) for key in VIDEO_TAG_LIST_FIELDS}
    for key in VIDEO_TAG_TEXT_FIELDS:
        tags[key] = str(source.get(key, "") or "").strip()
    return tags


def raw_segments(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        segments = data.get("video_lens")
        if isinstance(segments, list):
            return [item for item in segments if isinstance(item, dict)]
        for key in ("data", "result", "segments", "content", "text", "message", "output", "answer"):
            nested = data.get(key)
            if isinstance(nested, str):
                nested = extract_json(nested)
            if isinstance(nested, dict):
                return raw_segments(nested)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
    return []


def normalize_segments(raw: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for item in raw:
        try:
            description, summary, start_time, end_time = get_lens_data(item)
        except (TypeError, ValueError):
            continue
        start_time = max(0.0, min(start_time, duration))
        end_time = max(0.0, min(end_time, duration))
        if end_time <= start_time:
            continue
        slot, sub_slot, sub_slot_desc = normalize_tags(item)
        segments.append(
            {
                "description": description,
                "summary": summary,
                "start_time": round(start_time, 3),
                "end_time": round(end_time, 3),
                "slot": slot,
                "sub_slot": sub_slot,
                "sub_slot_desc": sub_slot_desc,
            }
        )
    segments.sort(key=lambda item: item["start_time"])
    return segments


def coverage_ok_lenses(segments: list[dict[str, Any]], duration: float, tolerance: float = 0.1) -> bool:
    if not segments:
        return False
    if float(segments[0]["start_time"]) > tolerance:
        return False
    if float(segments[-1]["end_time"]) < duration - tolerance:
        return False
    for left, right in zip(segments, segments[1:], strict=False):
        gap = float(right["start_time"]) - float(left["end_time"])
        if abs(gap) > tolerance:
            return False
    return True


def validate_segments(segments: list[dict[str, Any]], duration: float, fps: float = 30.0) -> list[dict[str, Any]]:
    if not segments:
        return []
    frame = 1.0 / max(fps, 1e-6)
    cleaned = sorted(
        (dict(segment) for segment in segments),
        key=lambda item: (float(item["start_time"]), float(item["end_time"])),
    )
    result: list[dict[str, Any]] = []
    previous_end = -1.0
    for index, segment in enumerate(cleaned):
        start = 0.0 if index == 0 else max(float(segment["start_time"]), previous_end + frame)
        end = duration if index == len(cleaned) - 1 else float(segment["end_time"])
        end = min(max(end, start + frame), duration)
        if end <= start:
            continue
        slot, sub_slot, sub_slot_desc = normalize_tags(segment)
        result.append(
            {
                "index": len(result) + 1,
                "start_time": round(start, 3),
                "end_time": round(end, 3),
                "description": str(segment.get("description") or ""),
                "summary": str(segment.get("summary") or ""),
                "slot": slot,
                "sub_slot": sub_slot,
                "sub_slot_desc": sub_slot_desc,
            }
        )
        previous_end = end
    if result:
        result[0]["start_time"] = 0.0
        result[-1]["end_time"] = round(duration, 3)
    return result


def seconds_to_ms(value: float) -> int:
    return int(round(float(value) * 1000))


def to_production_segments(
    lenses: list[dict[str, Any]],
    duration_ms: int,
    production_id: str,
) -> list[dict[str, Any]]:
    duration_ms = max(int(duration_ms), 1)
    segments: list[dict[str, Any]] = []
    for index, lens in enumerate(lenses, start=1):
        start_ms = seconds_to_ms(lens["start_time"]) if "start_time" in lens else int(lens.get("start_ms") or 0)
        end_ms = seconds_to_ms(lens["end_time"]) if "end_time" in lens else int(lens.get("end_ms") or 0)
        if index == 1:
            start_ms = 0
        if index == len(lenses):
            end_ms = duration_ms
        start_ms = min(max(start_ms, 0), duration_ms)
        end_ms = min(max(end_ms, start_ms + 1), duration_ms)
        slot, sub_slot, sub_slot_desc = normalize_tags(lens)
        role = role_from_slot(slot) if slot else str(lens.get("role") or "other")
        if role not in {"hook", "value", "cta", "other"}:
            role = "other"
        # Model-provided identifiers are untrusted and must never become paths.
        seg_id = f"seg_{index:02d}"
        clip_path = f"clips/{seg_id}.mp4"
        summary = str(lens.get("summary") or "").strip() or str(lens.get("description") or "").strip()
        segments.append(
            {
                "id": seg_id,
                "role": role,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "summary": summary,
                "description": str(lens.get("description") or "").strip(),
                "visual": str(lens.get("visual") or ""),
                "audio": str(lens.get("audio") or ""),
                "on_screen_text": str(lens.get("on_screen_text") or role.upper()),
                "reuse_recommended": bool(lens["reuse_recommended"])
                if "reuse_recommended" in lens
                else role == "value",
                "slot": slot,
                "sub_slot": sub_slot,
                "sub_slot_desc": sub_slot_desc,
                "clip_path": clip_path,
                "clip_url": f"/api/productions/{production_id}/artifacts/{clip_path}",
                "lens_info": dict(lens.get("lens_info") or {}),
                "error": lens.get("error"),
            }
        )
        observe = lens.get("observe")
        if isinstance(observe, dict) and not observe_is_empty(observe):
            segments[-1]["observe"] = observe
    _fix_ms_coverage(segments, duration_ms)
    return segments


def _fix_ms_coverage(segments: list[dict[str, Any]], duration_ms: int) -> None:
    if not segments:
        return
    segments[0]["start_ms"] = 0
    for left, right in zip(segments, segments[1:], strict=False):
        if right["start_ms"] < left["end_ms"]:
            mid = (left["end_ms"] + right["start_ms"]) // 2
            left["end_ms"] = mid
            right["start_ms"] = mid
        elif right["start_ms"] > left["end_ms"]:
            right["start_ms"] = left["end_ms"]
    segments[-1]["end_ms"] = duration_ms
    for item in segments:
        if item["end_ms"] <= item["start_ms"]:
            item["end_ms"] = min(item["start_ms"] + 1, duration_ms)


def coverage_ok(segments: list[dict[str, Any]], duration_ms: int, tolerance_ms: int = 100) -> bool:
    if not segments:
        return False
    if abs(int(segments[0]["start_ms"]) - 0) > tolerance_ms:
        return False
    if abs(int(segments[-1]["end_ms"]) - duration_ms) > tolerance_ms:
        return False
    for left, right in zip(segments, segments[1:], strict=False):
        if int(right["start_ms"]) < int(left["end_ms"]):
            return False
        if int(right["start_ms"]) - int(left["end_ms"]) > tolerance_ms:
            return False
    return True


def observe_is_empty(observe: Any) -> bool:
    if not isinstance(observe, dict):
        return True
    if observe.get("shot_observation_card"):
        return False
    if observe.get("raw_claims"):
        return False
    speech = observe.get("speech_evidence") or {}
    if isinstance(speech, dict):
        if speech.get("segments"):
            return False
        status = str(speech.get("status") or "not_used")
        if status not in {"", "not_used"}:
            return False
    return True


def ensure_segment_summaries(segments: list[dict[str, Any]], fallback: str = "") -> None:
    for item in segments:
        if not str(item.get("summary") or "").strip():
            item["summary"] = (
                str(item.get("description") or "").strip()
                or fallback
                or f"{str(item.get('role') or 'other').upper()} 段"
            )
