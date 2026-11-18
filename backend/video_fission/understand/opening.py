"""视频开头区间：模型结果解析、只一次小窗口吸附、无 Key 本地暂估。"""

from __future__ import annotations

from typing import Any

from .normalize import parse_time, seconds_to_ms

DEFAULT_SNAP_WINDOW_MS = 600
LOCAL_SEARCH_MIN_MS = 8000
LOCAL_SEARCH_MAX_MS = 18000
SHORT_FILM_MS = 12000
EXPLICIT_REPLACE_REASONS = {
    "user_selected",
    "role_selected",
    "time_range",
    "brief",
    "generate_all",
}


def parse_opening(data: Any, duration_ms: int) -> dict[str, Any] | None:
    """从模型 JSON 读取 opening。无效时返回 None，不得用 10–15 秒冒充模型结果。"""
    if not isinstance(data, dict):
        return None
    raw = data.get("opening")
    if not isinstance(raw, dict) or not raw:
        return None
    end_value = raw.get("end_time", raw.get("end_ms", raw.get("end")))
    if end_value in (None, ""):
        return None
    try:
        end_s = parse_time(end_value, last=True)
    except (TypeError, ValueError):
        return None
    end_ms = seconds_to_ms(end_s)
    if end_ms <= 0:
        return None
    duration_ms = max(int(duration_ms), 1)
    end_ms = min(end_ms, duration_ms)
    if end_ms <= 0:
        return None
    reason = str(raw.get("reason") or "").strip()
    return {
        "start_ms": 0,
        "end_ms": end_ms,
        "reason": reason,
        "method": "model",
        "segment_ids": [],
    }


def snap_opening_end(
    end_ms: int,
    *,
    duration_ms: int,
    cuts_ms: list[int] | None = None,
    silence_ms: list[int] | None = None,
    window_ms: int = DEFAULT_SNAP_WINDOW_MS,
    segment_bounds_ms: list[int] | None = None,
    frame_ms: int = 40,
) -> int:
    """只在模型 end 的 ±window 内吸附一次。窗口外切点不得采用。"""
    duration_ms = max(int(duration_ms), 1)
    end_ms = int(end_ms)
    window_ms = max(int(window_ms), 0)
    low = end_ms - window_ms
    high = end_ms + window_ms

    def in_window(points: list[int] | None) -> list[int]:
        return [int(point) for point in points or [] if low <= int(point) <= high and 0 < int(point) <= duration_ms]

    silences = in_window(silence_ms)
    cuts = in_window(cuts_ms)
    bounds = in_window(segment_bounds_ms)
    # 窗口内句界与切点冲突时优先句末，避免切断一口播。
    if silences:
        chosen = min(silences, key=lambda point: (abs(point - end_ms), point))
    elif cuts or bounds:
        chosen = min([*cuts, *bounds], key=lambda point: (abs(point - end_ms), point))
    else:
        chosen = end_ms
    return _legalize(chosen, duration_ms, frame_ms=frame_ms)


def _legalize(end_ms: int, duration_ms: int, frame_ms: int = 40) -> int:
    end_ms = int(end_ms)
    if end_ms > duration_ms and end_ms - duration_ms <= max(int(frame_ms), 1):
        end_ms = duration_ms
    end_ms = min(max(end_ms, 1), duration_ms)
    return end_ms


def estimate_local_opening(
    duration_ms: int,
    cuts_ms: list[int] | None = None,
    silence_ms: list[int] | None = None,
) -> dict[str, Any]:
    """无模型时的本地暂估。必须标 local_fallback，不得冒充模型开头。"""
    duration_ms = max(int(duration_ms), 1)
    if duration_ms <= SHORT_FILM_MS:
        end_ms = duration_ms
        reason = "短片，本地暂估将开头标到全片结束；这不是模型识别结果。"
    else:
        low, high = LOCAL_SEARCH_MIN_MS, min(LOCAL_SEARCH_MAX_MS, duration_ms)
        points = [int(p) for p in list(silence_ms or []) + list(cuts_ms or []) if low <= int(p) <= high]
        if points:
            mid = (low + high) // 2
            end_ms = min(points, key=lambda point: (abs(point - mid), point))
            reason = "无模型结果，已在约 8–18 秒搜索窗内按真实切点或句界做本地暂估。"
        else:
            later = [int(p) for p in list(silence_ms or []) + list(cuts_ms or []) if int(p) > LOCAL_SEARCH_MIN_MS and int(p) <= duration_ms]
            if later:
                end_ms = min(later)
                reason = "无模型结果，8–18 秒窗内没有切点，已用其后最近的真实切点作本地暂估。"
            else:
                end_ms = duration_ms
                reason = "无模型结果，且没有可用切点，本地暂估不硬切，开头标到全片结束。"
    return {
        "start_ms": 0,
        "end_ms": _legalize(end_ms, duration_ms),
        "reason": reason,
        "method": "local_fallback",
        "segment_ids": [],
    }


def opening_segment_ids(segments: list[dict[str, Any]], end_ms: int) -> list[str]:
    ids: list[str] = []
    end_ms = int(end_ms)
    for segment in segments or []:
        start = int(segment.get("start_ms") or 0)
        if start < end_ms or start == 0:
            if start < end_ms:
                segment_id = str(segment.get("id") or "")
                if segment_id:
                    ids.append(segment_id)
    return ids


def attach_opening_clip(opening: dict[str, Any], production_id: str) -> dict[str, Any]:
    item = dict(opening)
    item["clip_path"] = "clips/opening.mp4"
    item["clip_url"] = f"/api/productions/{production_id}/artifacts/clips/opening.mp4"
    return item


def finalize_opening(
    opening: dict[str, Any],
    segments: list[dict[str, Any]],
    duration_ms: int,
    production_id: str = "",
) -> dict[str, Any]:
    item = dict(opening)
    item["start_ms"] = 0
    item["end_ms"] = _legalize(int(item.get("end_ms") or 0), duration_ms)
    item["segment_ids"] = opening_segment_ids(segments, item["end_ms"])
    if production_id:
        item = attach_opening_clip(item, production_id)
    return item


def opening_block_generate(plan: dict[str, Any] | None, opening: dict[str, Any] | None, generate_all: bool = False) -> bool:
    """默认 hook/cta 不会把开头块改成 generate；点名开头内任一段则整块重做。"""
    if generate_all:
        return True
    if not opening:
        return False
    ids = set(opening.get("segment_ids") or [])
    plan = plan or {}
    policy = plan.get("policy") or {}
    if policy.get("mode") == "all":
        return True
    for reason in plan.get("reasons") or []:
        if reason.get("segment_id") in ids and reason.get("reason") in EXPLICIT_REPLACE_REASONS:
            return True
    return False


def expand_opening_plan(plan: dict[str, Any] | None, opening: dict[str, Any] | None, segments: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    if not plan or not opening or not opening_block_generate(plan, opening):
        return plan
    ids = list(opening.get("segment_ids") or [])
    resolved = list(plan.get("resolved_segment_ids") or [])
    reasons = list(plan.get("reasons") or [])
    existing = set(resolved)
    known = {item.get("id"): item for item in segments or []}
    for segment_id in ids:
        if segment_id in existing:
            continue
        resolved.append(segment_id)
        reasons.append({"segment_id": segment_id, "reason": "opening_block"})
        existing.add(segment_id)
        _ = known
    return {**plan, "resolved_segment_ids": resolved, "reasons": reasons}
