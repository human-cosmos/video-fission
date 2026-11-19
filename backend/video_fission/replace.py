from __future__ import annotations

import re
from typing import Any

from .understand.opening import expand_opening_plan, opening_block_generate

ALLOWED_MODES = ("default", "all", "selected", "brief")
ALLOWED_ROLES = ("hook", "value", "cta", "other")
NO_HIT_MESSAGE = "没有命中任何片段"
DEFAULT_POLICY = {
    "mode": "default",
    "roles": [],
    "segment_ids": [],
    "time_ranges": [],
}

_CN_ORDINAL = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

_REWRITE = re.compile(
    r"重新生成|再生成|新生成|重做|重制|再做|只做|只重做|只重|替换|换掉|换成|"
    r"regenerate|re-?generate|\bredo\b|\breplace\b",
    re.I,
)
_ALL_GENERATE = re.compile(r"全部新生成|全部生成|all\s*generate|generate\s*all", re.I)
_REUSE_MOST = re.compile(r"尽量复用|复用原片|\breuse\b", re.I)
_HOOK = re.compile(r"开场|开头|\bhook\b", re.I)
_VALUE = re.compile(r"中间|价值段|商品展示|\bvalue\b", re.I)
_CTA = re.compile(r"结尾|\bcta\b|行动号召", re.I)
_ALSO_DEFAULT = re.compile(r"也要|也得|同样|as well|also", re.I)
_ONLY = re.compile(r"只重做|只做|仅重做|仅做|只生成|仅生成|\bonly\b", re.I)
_ORDINAL = re.compile(
    r"第\s*([0-9]+|[一二三四五六七八九十两])\s*(?:个)?(?:段|片段|镜头)",
    re.I,
)
_SEG_ID = re.compile(r"\bseg[_\s-]?0*([0-9]+)\b", re.I)
_SHOT_N = re.compile(r"(?:镜头|片段|shot)\s*([0-9]+|[一二三四五六七八九十两])", re.I)
_SECONDS_RANGE = re.compile(
    r"(?:从\s*)?(\d+(?:\.\d+)?)\s*(?:秒|s)\s*(?:到|至|-|~|—|–)\s*(\d+(?:\.\d+)?)\s*(?:秒|s)?",
    re.I,
)
_CLOCK_RANGE = re.compile(
    r"(?:从\s*)?(\d{1,2}):(\d{2}(?:\.\d+)?)\s*(?:到|至|-|~|—|–)\s*(\d{1,2}):(\d{2}(?:\.\d+)?)",
    re.I,
)


def normalize_replace_policy(raw: Any) -> dict[str, Any]:
    if raw is None or raw == "":
        return dict(DEFAULT_POLICY)
    if not isinstance(raw, dict):
        raise ValueError("replace_policy 必须是对象")
    mode = str(raw.get("mode") or "default").strip().lower()
    if mode not in ALLOWED_MODES:
        raise ValueError("replace_policy.mode 只能是 default|all|selected|brief")
    roles: list[str] = []
    for item in raw.get("roles") or []:
        role = str(item).strip().lower()
        if role not in ALLOWED_ROLES:
            raise ValueError("roles 只允许 hook|value|cta|other")
        if role not in roles:
            roles.append(role)
    segment_ids: list[str] = []
    for item in raw.get("segment_ids") or []:
        segment_id = str(item).strip()
        if segment_id and segment_id not in segment_ids:
            segment_ids.append(segment_id)
    time_ranges: list[dict[str, int]] = []
    for item in raw.get("time_ranges") or []:
        if not isinstance(item, dict):
            continue
        try:
            start_ms = int(item.get("start_ms"))
            end_ms = int(item.get("end_ms"))
        except (TypeError, ValueError) as exc:
            raise ValueError("time_ranges 需要整数 start_ms / end_ms") from exc
        if end_ms <= start_ms:
            raise ValueError("time_ranges 的 end_ms 必须大于 start_ms")
        time_ranges.append({"start_ms": start_ms, "end_ms": end_ms})
    return {
        "mode": mode,
        "roles": roles,
        "segment_ids": segment_ids,
        "time_ranges": time_ranges,
    }



_REWRITE_NOISE = re.compile(
    r"把|将|请|帮我|一下|一个|个|和|与|以及|再|也要|也得|同样|也|要|的|了|吧|啊|"
    r"全部|所有|整个|原片|参考片|"
    r"开场|开头|中间|结尾|价值段|商品展示|行动号召|创意|想法|方案|hook|value|cta|段|镜头|片段",
    re.I,
)


def is_rewrite_only_brief(brief: str) -> bool:
    return bool((brief or "").strip()) and not strip_rewrite_instructions(brief)


def _clause_is_rewrite_only(text: str) -> bool:
    if not (_REWRITE.search(text) or _ALL_GENERATE.search(text) or _REUSE_MOST.search(text)):
        return False
    residual = text
    for pattern in (
        _REWRITE,
        _ALL_GENERATE,
        _REUSE_MOST,
        _ORDINAL,
        _SEG_ID,
        _SHOT_N,
        _SECONDS_RANGE,
        _CLOCK_RANGE,
    ):
        residual = pattern.sub(" ", residual)
    residual = _REWRITE_NOISE.sub(" ", residual)
    residual = re.sub(r"[\s，,。、！？!?；;：:（）()【】\[\]《》]+", "", residual)
    return len(residual) <= 1


def strip_rewrite_instructions(brief: str) -> str:
    """Keep creative sentences; drop regenerate/redo-only clauses."""
    text = re.sub(r"\s+", " ", (brief or "").strip())
    if not text:
        return ""
    if not (_REWRITE.search(text) or _ALL_GENERATE.search(text) or _REUSE_MOST.search(text)):
        return text
    pieces = re.split(r"([。！？!?；;\n])", text)
    kept: list[str] = []
    index = 0
    while index < len(pieces):
        sentence = pieces[index]
        delim = pieces[index + 1] if index + 1 < len(pieces) else ""
        index += 2
        clauses = [part.strip() for part in re.split(r"[，,、]", sentence) if part.strip()]
        useful = [part for part in clauses if not _clause_is_rewrite_only(part)]
        if not useful:
            continue
        ending = delim if delim and delim not in {"\n", ""} else "。"
        kept.append("，".join(useful) + (ending if ending != "\n" else "。"))
    return re.sub(r"\s+", " ", "".join(kept)).strip(" ，,。；;\n")


def parse_brief_targets(brief: str) -> dict[str, Any]:
    text = brief or ""
    generate_all = bool(_ALL_GENERATE.search(text))
    reuse_most = bool(_REUSE_MOST.search(text)) and not generate_all
    rewrite = bool(_REWRITE.search(text))
    ordinals: list[int] = []
    segment_ids: list[str] = []
    roles: list[str] = []
    time_ranges: list[dict[str, int]] = []

    def add_ordinal(raw: str) -> None:
        value = _parse_ordinal(raw)
        if value and value not in ordinals:
            ordinals.append(value)

    for match in _SEG_ID.finditer(text):
        add_ordinal(match.group(1))
        segment_ids.append(f"seg_{int(match.group(1)):02d}")

    # Ordinals and shot numbers are explicit even without a rewrite verb.
    for match in _ORDINAL.finditer(text):
        add_ordinal(match.group(1))
    for match in _SHOT_N.finditer(text):
        add_ordinal(match.group(1))

    for match in _SECONDS_RANGE.finditer(text):
        start_ms = int(round(float(match.group(1)) * 1000))
        end_ms = int(round(float(match.group(2)) * 1000))
        if end_ms > start_ms:
            time_ranges.append({"start_ms": start_ms, "end_ms": end_ms})
    for match in _CLOCK_RANGE.finditer(text):
        start_ms = _clock_to_ms(match.group(1), match.group(2))
        end_ms = _clock_to_ms(match.group(3), match.group(4))
        if end_ms > start_ms:
            time_ranges.append({"start_ms": start_ms, "end_ms": end_ms})

    named_without_roles = bool(ordinals or segment_ids or time_ranges)
    # Role words in a normal creative brief ("开场抓人、中间展示、结尾促单")
    # are not targets unless the user is asking to regenerate something.
    if rewrite and not generate_all:
        if _HOOK.search(text):
            roles.append("hook")
        if _VALUE.search(text):
            roles.append("value")
        if _CTA.search(text):
            roles.append("cta")

    also_default = bool(rewrite and _ALSO_DEFAULT.search(text) and not _ONLY.search(text))
    has_named = bool(roles or named_without_roles)
    return {
        "generate_all": generate_all,
        "reuse_most": reuse_most and not has_named,
        "roles": roles,
        "ordinals": ordinals,
        "segment_ids": segment_ids,
        "time_ranges": time_ranges,
        "also_default": also_default,
        "has_named": has_named,
    }


def resolve_replace_plan(
    policy: Any,
    segments: list[dict[str, Any]] | None,
    brief: str = "",
    opening: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = normalize_replace_policy(policy)
    segments = list(segments or [])
    resolved: list[str] = []
    reasons: list[dict[str, str]] = []

    def add(segment: dict[str, Any], reason: str) -> None:
        segment_id = str(segment.get("id") or "")
        if not segment_id or segment_id in resolved:
            return
        resolved.append(segment_id)
        reasons.append({"segment_id": segment_id, "reason": reason})

    if policy["mode"] == "all":
        for segment in segments:
            add(segment, "generate_all")
    elif policy["mode"] == "selected":
        selected_ids = set(policy["segment_ids"])
        selected_roles = set(policy["roles"])
        for segment in segments:
            if segment.get("id") in selected_ids:
                add(segment, "user_selected")
            elif segment.get("role") in selected_roles:
                add(segment, "role_selected")
            elif any(_range_hits(segment, item) for item in policy["time_ranges"]):
                add(segment, "time_range")
        if not resolved:
            raise ValueError(NO_HIT_MESSAGE)
    else:
        targets = parse_brief_targets(brief)
        if targets["generate_all"]:
            for segment in segments:
                add(segment, "generate_all")
        elif targets["has_named"]:
            wanted_ids = set(targets["segment_ids"])
            wanted_roles = set(targets["roles"])
            wanted_ordinals = set(targets["ordinals"])
            for index, segment in enumerate(segments, start=1):
                if (
                    segment.get("id") in wanted_ids
                    or index in wanted_ordinals
                    or segment.get("role") in wanted_roles
                    or any(_range_hits(segment, item) for item in targets["time_ranges"])
                ):
                    add(segment, "brief")
            if targets["also_default"]:
                for segment in segments:
                    if segment.get("role") in {"hook", "cta"}:
                        add(segment, "default_hook_cta")
            if not resolved:
                raise ValueError(NO_HIT_MESSAGE)
        else:
            for segment in segments:
                if segment.get("role") in {"hook", "cta"}:
                    add(segment, "default_hook_cta")

    plan = {
        "policy": policy,
        "resolved_segment_ids": resolved,
        "reasons": reasons,
    }
    expanded = expand_opening_plan(plan, opening, segments)
    return expanded or plan


def apply_replace_plan(script: dict[str, Any], production: dict[str, Any]) -> dict[str, Any]:
    plan = production.get("replace_plan") or {}
    resolved = set(plan.get("resolved_segment_ids") or [])
    if not plan:
        return script
    opening = (production.get("decomposition") or {}).get("opening") or script.get("opening")
    shots = []
    for shot in script.get("shots") or []:
        segment_id = shot.get("source_segment_id")
        is_opening = (
            shot.get("source_kind") == "opening"
            or shot.get("id") == (script.get("opening") or {}).get("shot_id")
            or shot.get("id") == "shot_opening"
        )
        if is_opening:
            action = "generate" if opening_block_generate(plan, opening) else "reuse"
        else:
            action = "generate" if segment_id in resolved else "reuse"
        item = dict(shot)
        item["action"] = action
        if action == "generate":
            if not item.get("prompt"):
                item["prompt"] = item.get("visual") or production.get("brief") or "Generate a short product shot"
        else:
            item["prompt"] = ""
        shots.append(item)
    return {**script, "shots": shots}


def _range_hits(segment: dict[str, Any], window: dict[str, int]) -> bool:
    start = int(segment.get("start_ms") or 0)
    end = int(segment.get("end_ms") or 0)
    overlap = max(0, min(end, int(window["end_ms"])) - max(start, int(window["start_ms"])))
    duration = max(end - start, 0)
    return overlap >= 200 or (duration > 0 and overlap * 2 > duration)


def _parse_ordinal(raw: str) -> int | None:
    text = str(raw).strip()
    if text.isdigit():
        value = int(text)
        return value if value > 0 else None
    return _CN_ORDINAL.get(text)


def _clock_to_ms(minutes: str, seconds: str) -> int:
    return int(round((int(minutes) * 60 + float(seconds)) * 1000))
