"""observe 规范化与调用。"""

from __future__ import annotations

import copy
import json
import logging
from typing import Any

from video_fission import media

from .normalize import extract_json, observe_is_empty
from .prompts import PROMPT_LENS_OBSERVE

logger = logging.getLogger(__name__)

_TEXT_FIELDS = (
    "visual_subjects",
    "visual_actions",
    "setting_and_props",
    "camera_observation",
    "lighting_and_image_observation",
    "graphic_text_observation",
    "language_and_locale_observation",
    "information_presentation_observation",
    "speech_transcript",
    "audio_observation",
    "sequence_function_observation",
    "raw_claim_id",
    "raw_claim",
    "raw_claim_language",
    "claim_expression",
    "uncertainty_notes",
)

_CARD_DEFAULTS: dict[str, Any] = {
    "shot_id": None,
    "start_time": None,
    "end_time": None,
    "evidence_coverage": {
        "visual": "unavailable",
        "audio": "unavailable",
        "notes": "",
    },
    "visual_subjects": "",
    "visual_actions": "",
    "setting_and_props": "",
    "camera_observation": "",
    "lighting_and_image_observation": "",
    "graphic_text_observation": "",
    "language_and_locale_observation": "",
    "information_presentation_observation": "",
    "speech_transcript": "",
    "audio_observation": "",
    "sequence_function_observation": "",
    "raw_claim_id": "",
    "raw_claim": "",
    "raw_claim_source": "none",
    "raw_claim_scope": "none",
    "raw_claim_alignment": "none",
    "raw_claim_language": "",
    "ambient_raw_claims": [],
    "claim_expression": "",
    "review_questions": [],
    "uncertainty_notes": "",
    "review": {"status": "not_requested"},
}


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


def normalize_card(card: dict[str, Any], shot: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(_CARD_DEFAULTS)
    for key, value in card.items():
        if key in merged and key not in ("evidence_coverage", "review"):
            merged[key] = value
    coverage = card.get("evidence_coverage")
    if isinstance(coverage, dict):
        merged["evidence_coverage"].update(
            {
                "visual": coverage.get("visual", merged["evidence_coverage"]["visual"]),
                "audio": coverage.get("audio", merged["evidence_coverage"]["audio"]),
                "notes": coverage.get("notes") or "",
            }
        )
    merged["shot_id"] = shot["shot_id"]
    merged["start_time"] = shot.get("start_time")
    merged["end_time"] = shot.get("end_time")
    for key in _TEXT_FIELDS:
        if merged.get(key) is None:
            merged[key] = ""
        elif not isinstance(merged[key], str):
            merged[key] = str(merged[key])
    if not isinstance(merged.get("review_questions"), list):
        merged["review_questions"] = []
    extra_questions = merged["review_questions"][2:]
    merged["review_questions"] = merged["review_questions"][:2]
    if extra_questions:
        note = merged["uncertainty_notes"]
        merged["uncertainty_notes"] = (note + " " if note else "") + "复查问题超过 2 条，其余未保留。"
    if not isinstance(merged.get("ambient_raw_claims"), list):
        merged["ambient_raw_claims"] = []
    merged["review"] = {"status": "not_requested"}
    return merged


def apply_audio_probe(card: dict[str, Any], has_audio: bool | None) -> None:
    if has_audio is not False:
        return
    coverage = card["evidence_coverage"]
    if coverage.get("audio") == "unavailable":
        coverage["audio"] = "full"
        note = coverage.get("notes") or ""
        if "已确认无音轨" not in note:
            coverage["notes"] = (note + " " if note else "") + "已确认无音轨"


def build_raw_claims(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for card in cards:
        entries = []
        if card.get("raw_claim_id") and card.get("raw_claim"):
            entries.append(
                (
                    card["raw_claim_id"],
                    card["raw_claim"],
                    card.get("raw_claim_source") or "none",
                    card.get("raw_claim_scope") or "none",
                    card.get("raw_claim_language") or "",
                    "primary",
                )
            )
        for ambient in card.get("ambient_raw_claims") or []:
            if isinstance(ambient, dict) and ambient.get("raw_claim_id") and ambient.get("raw_claim"):
                entries.append(
                    (
                        ambient["raw_claim_id"],
                        ambient["raw_claim"],
                        ambient.get("source_type") or ambient.get("raw_claim_source") or "persistent_onscreen_text",
                        ambient.get("scope") or "persistent_overlay",
                        ambient.get("language") or ambient.get("raw_claim_language") or "",
                        "ambient",
                    )
                )
        for claim_id, text, source, scope, language, usage in entries:
            item = grouped.setdefault(
                claim_id,
                {
                    "raw_claim_id": claim_id,
                    "raw_claim": text,
                    "source_type": source,
                    "scope": scope,
                    "language": language,
                    "shot_refs": [],
                    "start_time": None,
                    "end_time": None,
                    "usage": usage,
                },
            )
            if card["shot_id"] not in item["shot_refs"]:
                item["shot_refs"].append(card["shot_id"])
            if item["usage"] != usage:
                item["usage"] = "both"
            start = card.get("start_time")
            end = card.get("end_time")
            if start is None or end is None:
                item["time_incomplete"] = True
                item["start_time"] = None
                item["end_time"] = None
            elif not item.get("time_incomplete"):
                if item["start_time"] is None:
                    item["start_time"] = start
                    item["end_time"] = end
                else:
                    item["start_time"] = min(item["start_time"], start)
                    item["end_time"] = max(item["end_time"], end)
    claims = []
    for item in grouped.values():
        item.pop("time_incomplete", None)
        claims.append(item)
    return claims


def _offset_seconds(value: Any, offset: float) -> float | None:
    if value is None or value == "":
        return None
    try:
        return round(float(value) + offset, 3)
    except (TypeError, ValueError):
        return None


def normalize_speech(payload: dict[str, Any], original_start: float = 0.0) -> dict[str, Any]:
    speech = payload.get("speech_evidence")
    if not isinstance(speech, dict):
        return empty_speech()
    segments = []
    for item in speech.get("segments") or []:
        if not isinstance(item, dict):
            continue
        normalized = dict(item)
        for start_key in ("start", "start_time"):
            if start_key in normalized:
                normalized[start_key] = _offset_seconds(normalized[start_key], original_start)
        for end_key in ("end", "end_time"):
            if end_key in normalized:
                normalized[end_key] = _offset_seconds(normalized[end_key], original_start)
        segments.append(normalized)
    return {
        "status": str(speech.get("status") or "not_used"),
        "source": str(speech.get("source") or ""),
        "language": str(speech.get("language") or ""),
        "segments": segments,
        "notes": str(speech.get("notes") or ""),
    }


def build_task(clip_duration: float, has_audio: bool | None, original_start: float) -> str:
    shot = {"shot_id": 1, "start_time": 0.0, "end_time": round(clip_duration, 3)}
    task = {
        "task": (
            "基础观察。整段视频只有一个目标单元，不要拆镜，不要写局部复查。"
            'review 固定为 {"status": "not_requested"}。描述性文字使用中文；'
            "口播、画面原文和 raw_claim 保持原语言。"
        ),
        "time_origin": "provided_clip",
        "original_offset_seconds": round(original_start, 3),
        "duration_seconds": round(clip_duration, 3),
        "has_audio": has_audio,
        "target_shots": [shot],
        "context_shots_do_not_output": [],
        "output_keys": ["shot_observation_card", "speech_evidence", "issues"],
        "rules": [
            "每张卡的 shot_id 必须来自 target_shots",
            "speech_transcript 只写本单元实际听到的部分",
            "同一句话跨单元时 raw_claim_id 和 raw_claim 必须相同",
            "review_questions 最多 2 条",
            "speech_evidence 只收录与本批目标时间相交的句段；没有转写则 status=not_used",
            "has_audio=false 时不要编造口播，audio 覆盖写明已确认无音轨",
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
    has_audio: bool | None = None,
    original_start: float = 0.0,
) -> dict[str, Any]:
    result = empty_observe()
    shot = {
        "shot_id": 1,
        "start_time": round(original_start, 3),
        "end_time": round(original_start + clip_duration, 3),
    }
    raw_cards = [item for item in payload.get("shot_observation_card") or [] if isinstance(item, dict)]
    selected = next(
        (card for card in raw_cards if str(card.get("shot_id")) == "1"), raw_cards[0] if raw_cards else None
    )
    cards: list[dict[str, Any]] = []
    if selected is not None:
        normalized = normalize_card(selected, shot)
        apply_audio_probe(normalized, has_audio)
        cards.append(normalized)
    result["shot_observation_card"] = cards
    result["raw_claims"] = build_raw_claims(cards)
    result["speech_evidence"] = normalize_speech(payload, original_start)
    result["time_origin"] = "original_video"
    result["original_offset_seconds"] = round(original_start, 3)
    return result


def observe_prompt(clip_duration: float, has_audio: bool | None, original_start: float) -> str:
    return PROMPT_LENS_OBSERVE.replace("__TASK_JSON__", build_task(clip_duration, has_audio, original_start), 1)


# re-export for tests
__all__ = [
    "empty_observe",
    "empty_speech",
    "normalize_card",
    "normalize_observe_response",
    "normalize_speech",
    "build_raw_claims",
    "extract_observe_payload",
    "observe_prompt",
    "observe_is_empty",
    "probe_has_audio",
]
