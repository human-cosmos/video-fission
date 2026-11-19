from __future__ import annotations

import copy
import re
from typing import Any

from .landing import extract_urls
from .replace import strip_rewrite_instructions

CANDIDATE_SPECS = (
    {
        "id": "script_a",
        "title": "痛点切入",
        "angle": "先点出用户正被卡住的问题，再给出产品和行动理由。",
    },
    {
        "id": "script_b",
        "title": "效果利益",
        "angle": "先展示使用后的具体好处，再用卖点把效果说清楚。",
    },
    {
        "id": "script_c",
        "title": "信任促单",
        "angle": "用承诺和证据建立信任，再明确告诉观众下一步怎么做。",
    },
)

_ORDINALS = {
    "1": "script_a",
    "2": "script_b",
    "3": "script_c",
    "一": "script_a",
    "二": "script_b",
    "两": "script_b",
    "三": "script_c",
    "a": "script_a",
    "b": "script_b",
    "c": "script_c",
}

_SELECT_RE = re.compile(
    r"(?:用|选|选择|就用)\s*(?:第\s*)?([123一二两三abcABC])\s*(?:个|号|套)?(?:脚本|方案)?"
    r"|第\s*([123一二两三abcABC])\s*(?:个|号|套)(?:脚本|方案)?"
)
_SELECT_ID_RE = re.compile(r"script[_-]?([abcABC])")
_SCRIPT_REWRITE_RE = re.compile(
    r"换脚本|再出脚本|重新写脚本|再给.{0,6}脚本|改卖点|改文案|不要这[几个份套]|重做脚本|换一[批组套]脚本"
)
_GENERATE_RE = re.compile(
    r"再生成(?:视频|一遍|一版)?|改画面|重新出片|按这个再生成|再出片|重跑生成|画面再来|重新生成(?!脚本)"
)


def lock_script_to_timeline(script: dict[str, Any], timeline: dict[str, Any]) -> dict[str, Any]:
    locked = copy.deepcopy(timeline)
    incoming_shots = list((script or {}).get("shots") or [])
    for index, shot in enumerate(locked.get("shots") or []):
        source = incoming_shots[index] if index < len(incoming_shots) else {}
        for key in ("visual", "voiceover", "on_screen_text", "prompt"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                shot[key] = value.strip()
        # Timeline wins for timing and reuse/opening structure.
        for key in ("start_ms", "end_ms", "duration_ms", "role", "action", "source_segment_id", "id"):
            if key in (timeline.get("shots") or [])[index]:
                shot[key] = copy.deepcopy(timeline["shots"][index][key])
    if timeline.get("opening"):
        locked["opening"] = copy.deepcopy(timeline["opening"])
    locked["total_duration_ms"] = timeline.get("total_duration_ms") or locked.get("total_duration_ms")
    if timeline.get("audio_plan") and not locked.get("audio_plan"):
        locked["audio_plan"] = copy.deepcopy(timeline["audio_plan"])
    return locked


def build_local_candidates(production: dict[str, Any], timeline: dict[str, Any]) -> list[dict[str, Any]]:
    landing = production.get("landing_page") or {}
    points = [str(item).strip() for item in (landing.get("selling_points") or []) if str(item).strip()]
    product = str(landing.get("product") or "").strip() or "产品"
    promise = str(((production.get("intent") or {}).get("promise") or "")).strip()
    brief = str(production.get("brief") or "").strip()
    creative = strip_rewrite_instructions(brief)
    hook_point = points[0] if points else (creative[:24] or "先抓住痛点")
    benefit_point = points[1] if len(points) > 1 else (promise or hook_point)
    trust_point = points[2] if len(points) > 2 else (promise or "现在就去看看")
    variants = {
        "script_a": {
            "hook": f"还在为「{hook_point}」发愁吗？",
            "value": f"{product}就是为这个情况准备的。",
            "cta": "看完直接上手，别再拖着。",
        },
        "script_b": {
            "hook": f"用上之后，{benefit_point}会立刻被感受到。",
            "value": f"{product}把效果落到能看见的变化上。",
            "cta": "想要同样结果，现在就开始。",
        },
        "script_c": {
            "hook": f"先把承诺说清楚：{trust_point}。",
            "value": f"{product}用可核对的细节建立信任。",
            "cta": "认准这一点，再完成下单或咨询。",
        },
    }
    candidates: list[dict[str, Any]] = []
    for spec in CANDIDATE_SPECS:
        script = copy.deepcopy(timeline)
        lines = variants[spec["id"]]
        shots = script.get("shots") or []
        if shots:
            _rewrite_shot(shots[0], lines["hook"], "hook")
        if len(shots) >= 3:
            _rewrite_shot(shots[1], lines["value"], "value")
            _rewrite_shot(shots[-1], lines["cta"], "cta")
        elif len(shots) == 2:
            _rewrite_shot(shots[1], f"{lines['value']} {lines['cta']}", "cta")
        elif shots:
            shot = shots[0]
            shot["voiceover"] = f"{lines['hook']}{lines['value']}{lines['cta']}"
            shot["on_screen_text"] = lines["hook"]
        candidates.append(
            {
                "id": spec["id"],
                "title": spec["title"],
                "angle": spec["angle"],
                "script": script,
            }
        )
    return candidates


def parse_script_selection(text: str, candidates: list[dict[str, Any]] | None = None) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None
    match_id = _SELECT_ID_RE.search(raw)
    if match_id:
        return f"script_{match_id.group(1).lower()}"
    match = _SELECT_RE.search(raw)
    if match:
        token = (match.group(1) or match.group(2) or "").lower()
        return _ORDINALS.get(token)
    lowered = raw.lower()
    for item in candidates or []:
        title = str(item.get("title") or "")
        ident = str(item.get("id") or "")
        if title and title in raw:
            return ident
        if ident and ident.lower() in lowered:
            return ident
    return None


def classify_followup(production: dict[str, Any], text: str) -> tuple[str, str]:
    """Return ('select', script_id) or ('resume', stage)."""
    status = str(production.get("status") or "")
    selected = str(production.get("selected_script_id") or "").strip()
    candidates = production.get("script_candidates") or []
    choice = parse_script_selection(text, candidates)
    rewrite = bool(_SCRIPT_REWRITE_RE.search(text or ""))
    generate = bool(_GENERATE_RE.search(text or ""))
    new_landing = bool(extract_urls(text or "")) or ("卖点" in (text or ""))
    if choice and not rewrite:
        return "select", choice
    if rewrite or new_landing:
        return "resume", "intent" if new_landing else "script"
    if generate and selected:
        return "resume", "generate"
    if status == "awaiting_script_choice":
        if choice:
            return "select", choice
        return "resume", "script"
    if selected:
        return "resume", "generate"
    return "resume", "intent"


def candidate_by_id(candidates: list[dict[str, Any]], script_id: str) -> dict[str, Any] | None:
    wanted = str(script_id or "").strip()
    for item in candidates or []:
        if str(item.get("id") or "").strip() == wanted:
            return item
    return None


def _rewrite_shot(shot: dict[str, Any], line: str, kind: str) -> None:
    existing = str(shot.get("voiceover") or "").strip()
    shot["voiceover"] = line if not existing else f"{line}{'' if line.endswith(('。', '！', '？', '.', '!', '?')) else '。'}"
    if kind in {"hook", "cta"} or not shot.get("on_screen_text"):
        shot["on_screen_text"] = line[:24]
    visual = str(shot.get("visual") or "").strip()
    if visual:
        shot["visual"] = f"{visual}（{line[:18]}）"
    else:
        shot["visual"] = line
    prompt = str(shot.get("prompt") or visual or line)
    shot["prompt"] = prompt
