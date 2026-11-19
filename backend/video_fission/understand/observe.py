"""observe 规范化与调用。"""

from __future__ import annotations

import json
import logging
import math
from typing import Any

from video_fission import media

from .normalize import extract_json, observe_is_empty
from .prompts import PROMPT_LENS_OBSERVE_COMPACT

logger = logging.getLogger(__name__)

OBSERVE_CARD_FIELDS = (
    "visual_subjects",
    "visual_actions",
    "setting_and_props",
    "camera_observation",
    "graphic_text_observation",
    "information_presentation_observation",
    "speech_transcript",
    "audio_observation",
    "sequence_function_observation",
    "claim_expression",
    "uncertainty_notes",
)

def empty_speech() -> dict[str, Any]:
    return {
        "status": "not_used",
        "source": "",
        "language": "",
        "segments": [],
        "notes": "",
    }


def empty_observe() -> dict[str, Any]:
    return {
        "shot_observation_card": [],
        "raw_claims": [],
        "speech_evidence": empty_speech(),
    }


def probe_has_audio(path: str) -> bool | None:
    try:
        info = media.probe(path)
        return bool(info.get("has_audio"))
    except Exception:  # noqa: BLE001
        return None


def normalize_card(card: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, str] = {}
    for key in OBSERVE_CARD_FIELDS:
        value = card.get(key)
        text = str(value).strip() if value is not None else ""
        if text:
            normalized[key] = text
    return normalized


def _claim_item(
    *,
    claim_id: Any,
    text: Any,
    source: Any,
    scope: Any,
    language: Any,
    start_time: float,
    end_time: float,
) -> dict[str, Any]:
    return {
        "raw_claim_id": str(claim_id or "").strip(),
        "raw_claim": str(text or "").strip(),
        "source_type": str(source or "none").strip(),
        "scope": str(scope or "none").strip(),
        "language": str(language or "").strip(),
        "start_time": round(start_time, 3),
        "end_time": round(end_time, 3),
    }


def normalize_claims(
    claims: Any,
    original_start: float,
    default_end: float,
) -> list[dict[str, Any]]:
    if not isinstance(claims, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in claims:
        if not isinstance(item, dict):
            continue
        claim_id = str(item.get("raw_claim_id") or item.get("id") or "").strip()
        text = str(item.get("raw_claim") or item.get("text") or "").strip()
        if not text or (claim_id and claim_id in seen):
            continue
        if claim_id:
            seen.add(claim_id)
        start = _offset_seconds(item.get("start_time", item.get("start")), original_start)
        end = _offset_seconds(item.get("end_time", item.get("end")), original_start)
        start, end = _bounded_range(start, end, original_start, default_end)
        result.append(
            _claim_item(
                claim_id=claim_id,
                text=text,
                source=item.get("source_type") or item.get("raw_claim_source"),
                scope=item.get("scope") or item.get("raw_claim_scope"),
                language=item.get("language") or item.get("raw_claim_language"),
                start_time=start,
                end_time=end,
            )
        )
    return result


def build_raw_claims(
    cards: list[dict[str, Any]],
    original_start: float,
    default_end: float,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    clip_duration = max(default_end - original_start, 0.0)
    for card in cards:
        if card.get("raw_claim_id") and card.get("raw_claim"):
            entries.append(
                {
                    "raw_claim_id": card["raw_claim_id"],
                    "raw_claim": card["raw_claim"],
                    "source_type": card.get("raw_claim_source"),
                    "scope": card.get("raw_claim_scope"),
                    "language": card.get("raw_claim_language"),
                    "start_time": card.get("start_time", 0.0),
                    "end_time": card.get("end_time", clip_duration),
                }
            )
        for ambient in card.get("ambient_raw_claims") or []:
            if isinstance(ambient, dict) and ambient.get("raw_claim_id") and ambient.get("raw_claim"):
                entries.append(
                    {
                        "raw_claim_id": ambient["raw_claim_id"],
                        "raw_claim": ambient["raw_claim"],
                        "source_type": ambient.get("source_type")
                        or ambient.get("raw_claim_source")
                        or "persistent_onscreen_text",
                        "scope": ambient.get("scope") or "persistent_overlay",
                        "language": ambient.get("language") or ambient.get("raw_claim_language"),
                        "start_time": 0.0,
                        "end_time": clip_duration,
                    }
                )
    return normalize_claims(entries, original_start, default_end)


def _offset_seconds(value: Any, offset: float) -> float | None:
    if value is None or value == "":
        return None
    try:
        seconds = float(value) + offset
    except (TypeError, ValueError):
        return None
    return round(seconds, 3) if math.isfinite(seconds) else None


def _bounded_range(
    start: float | None,
    end: float | None,
    lower: float,
    upper: float,
) -> tuple[float, float]:
    clip_start = round(lower, 3)
    clip_end = round(max(lower, upper), 3)
    start_value = clip_start if start is None else min(max(start, clip_start), clip_end)
    end_value = clip_end if end is None else min(max(end, clip_start), clip_end)
    if end_value < start_value:
        end_value = start_value
    return round(start_value, 3), round(end_value, 3)


def normalize_speech(
    payload: dict[str, Any],
    original_start: float = 0.0,
    default_end: float | None = None,
) -> dict[str, Any]:
    speech = payload.get("speech_evidence")
    if not isinstance(speech, dict):
        return empty_speech()
    segments = []
    for item in speech.get("segments") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        start = _offset_seconds(item.get("start", item.get("start_time")), original_start)
        end = _offset_seconds(item.get("end", item.get("end_time")), original_start)
        if default_end is not None:
            start, end = _bounded_range(start, end, original_start, default_end)
        segments.append({"start": start, "end": end, "text": text})
    status = str(speech.get("status") or "").strip()
    result: dict[str, Any] = {"status": status or ("complete" if segments else "not_used")}
    source = str(speech.get("source") or "").strip()
    language = str(speech.get("language") or "").strip()
    notes = str(speech.get("notes") or "").strip()
    if source:
        result["source"] = source
    if language:
        result["language"] = language
    if segments:
        result["segments"] = segments
    if notes:
        result["notes"] = notes
    return result


def build_task(clip_duration: float, has_audio: bool | None, original_start: float) -> str:
    shot = {"shot_id": 1, "start_time": 0.0, "end_time": round(clip_duration, 3)}
    task = {
        "task": (
            "深度观察。整段视频只有一个目标单元，不要拆镜。"
            "描述性文字使用中文；"
            "口播、画面原文和 raw_claim 保持原语言。"
        ),
        "time_origin": "provided_clip",
        "original_offset_seconds": round(original_start, 3),
        "duration_seconds": round(clip_duration, 3),
        "has_audio": has_audio,
        "target_shots": [shot],
        "context_shots_do_not_output": [],
        "output_keys": ["shot_observation_card", "raw_claims", "speech_evidence"],
        "rules": [
            "speech_transcript 只写本单元实际听到的部分",
            "同一句话跨单元时 raw_claim_id 和 raw_claim 必须相同",
            "每张卡最多输出一个主要主张，持续字幕主张放入 raw_claims",
            "raw_claims 的 start_time/end_time 使用片段本地秒数",
            "speech_evidence 只收录与本批目标时间相交的句段；没有转写则 status=not_used",
            "has_audio=false 时不要编造口播，并在 uncertainty_notes 写明已确认无音轨",
            "has_audio=null 时不要把听不到写成无声",
            "描述性文字使用中文",
            "speech_transcript、画面原文和 raw_claim 保持原语言，不翻译",
        ],
    }
    return json.dumps(task, ensure_ascii=False)


def extract_observe_payload(response: dict[str, Any] | str) -> dict[str, Any] | None:
    data: Any = response.get("data") if isinstance(response, dict) else response
    for _ in range(4):
        if isinstance(data, dict):
            if "shot_observation_card" in data or "speech_evidence" in data:
                return data
            nested: Any = None
            for key in ("data", "result", "text", "content", "output", "message", "answer"):
                value = data.get(key)
                if isinstance(value, (dict, str)) and value:
                    nested = value
                    break
            if nested is None:
                return None
            data = nested
        elif isinstance(data, str):
            parsed = extract_json(data)
            if isinstance(parsed, list):
                parsed = next((item for item in parsed if isinstance(item, dict)), None)
            return parsed if isinstance(parsed, dict) else None
        else:
            return None
    return None


def normalize_observe_response(
    payload: dict[str, Any],
    clip_duration: float,
    original_start: float = 0.0,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    default_end = round(original_start + clip_duration, 3)
    raw_cards = [item for item in payload.get("shot_observation_card") or [] if isinstance(item, dict)]
    selected = next(
        (card for card in raw_cards if str(card.get("shot_id")) == "1"), raw_cards[0] if raw_cards else None
    )
    cards: list[dict[str, Any]] = []
    if selected is not None:
        normalized = normalize_card(selected)
        if any(normalized.values()):
            cards.append(normalized)
    if cards:
        result["shot_observation_card"] = cards
    claims = normalize_claims(payload.get("raw_claims"), original_start, default_end)
    if not claims and selected is not None:
        claims = build_raw_claims([selected], original_start, default_end)
    if claims:
        result["raw_claims"] = claims
    speech = normalize_speech(payload, original_start, default_end)
    if speech.get("segments") or speech.get("notes"):
        result["speech_evidence"] = speech
    return result


def observe_prompt(clip_duration: float, has_audio: bool | None, original_start: float) -> str:
    return PROMPT_LENS_OBSERVE_COMPACT.replace(
        "__TASK_JSON__",
        build_task(clip_duration, has_audio, original_start),
        1,
    )


# re-export for tests
__all__ = [
    "empty_observe",
    "empty_speech",
    "OBSERVE_CARD_FIELDS",
    "normalize_card",
    "normalize_claims",
    "normalize_observe_response",
    "normalize_speech",
    "build_raw_claims",
    "extract_observe_payload",
    "observe_prompt",
    "observe_is_empty",
    "probe_has_audio",
]
