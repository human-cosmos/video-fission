from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from . import media

_STYLE_LINE = re.compile(
    r"^Style:\s*([^,]+),([^,]+),([^,]+),([^,]+),([^,]+),([^,]+),([^,]+),"
    r"([^,]+),([^,]+),([^,]+),([^,]+),([^,]+),([^,]+),([^,]+),([^,]+),"
    r"([^,]+),([^,]+),([^,]+),([^,]+),([^,]+),([^,]+),([^,]+),([^,]+)\s*$",
    re.I | re.M,
)


def default_safe_style(width: int = 1280, height: int = 720) -> dict[str, Any]:
    layout = media.subtitle_burn_layout(width, height)
    return {
        "method": "default_safe",
        "aligned": False,
        "font_name": "Droid Sans Fallback",
        "font_size": layout["font_size"],
        "primary_colour": "&H00FFFFFF",
        "outline_colour": "&H00111111",
        "alignment": 2,
        "margin_v": layout["margin_v"],
        "margin_l": layout["margin_x"],
        "margin_r": layout["margin_x"],
        "note": "未读到参考字幕样式，使用安全默认位置",
    }


def detect_subtitle_style(source: Path, dest_dir: Path | None = None) -> dict[str, Any]:
    info = media.probe(source)
    width = int(info["width"])
    height = int(info["height"])
    fallback = default_safe_style(width, height)
    stream = _subtitle_stream(source)
    if not stream:
        return fallback
    work = Path(dest_dir or source.parent)
    work.mkdir(parents=True, exist_ok=True)
    extracted = work / "reference-subtitles.ass"
    if not _extract_subtitles(source, extracted):
        return fallback
    parsed = parse_ass_style(extracted, width, height)
    if not parsed:
        srt = work / "reference-subtitles.srt"
        if extracted.suffix != ".srt" and _extract_subtitles(source, srt, fmt="srt"):
            # SRT has no typography; keep default metrics but record source.
            fallback = default_safe_style(width, height)
            fallback.update(
                {
                    "method": "soft_sub",
                    "aligned": False,
                    "note": "参考含软字幕但未读到字体位置，使用安全默认样式硬烧",
                }
            )
            return fallback
        return fallback
    parsed["method"] = "soft_sub"
    parsed["aligned"] = True
    parsed["note"] = "按参考软字幕样式硬烧"
    return parsed


def parse_ass_style(path: Path, width: int, height: int) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    play_x = _int_after(text, "PlayResX:") or width
    play_y = _int_after(text, "PlayResY:") or height
    match = _STYLE_LINE.search(text)
    if not match:
        return None
    font_size = _safe_int(match.group(3), 0)
    alignment = _safe_int(match.group(19), 2)
    margin_l = _safe_int(match.group(20), 0)
    margin_r = _safe_int(match.group(21), 0)
    margin_v = _safe_int(match.group(22), 0)
    if play_y > 0 and play_y != height and margin_v:
        margin_v = max(1, round(margin_v * height / play_y))
    if play_x > 0 and play_x != width:
        if margin_l:
            margin_l = max(1, round(margin_l * width / play_x))
        if margin_r:
            margin_r = max(1, round(margin_r * width / play_x))
    if font_size and play_y > 0 and play_y != height:
        font_size = max(12, round(font_size * height / play_y))
    if not font_size and not margin_v:
        return None
    return {
        "font_name": match.group(2).strip() or "Droid Sans Fallback",
        "font_size": font_size or media.subtitle_burn_layout(width, height)["font_size"],
        "primary_colour": match.group(4).strip() or "&H00FFFFFF",
        "outline_colour": match.group(6).strip() or "&H00111111",
        "alignment": alignment or 2,
        "margin_v": margin_v or media.subtitle_burn_layout(width, height)["margin_v"],
        "margin_l": margin_l or media.subtitle_burn_layout(width, height)["margin_x"],
        "margin_r": margin_r or media.subtitle_burn_layout(width, height)["margin_x"],
    }


def layout_from_style(width: int, height: int, style: dict[str, Any] | None = None) -> dict[str, Any]:
    layout = media.subtitle_burn_layout(width, height)
    layout["font_name"] = "Droid Sans Fallback"
    layout["primary_colour"] = "&H00FFFFFF"
    layout["outline_colour"] = "&H00111111"
    layout["alignment"] = 2
    if not style or style.get("method") == "default_safe" or not style.get("aligned"):
        return layout
    if style.get("font_name"):
        layout["font_name"] = str(style["font_name"])
    if style.get("font_size"):
        layout["font_size"] = int(style["font_size"])
    if style.get("primary_colour"):
        layout["primary_colour"] = str(style["primary_colour"])
    if style.get("outline_colour"):
        layout["outline_colour"] = str(style["outline_colour"])
    if style.get("alignment"):
        layout["alignment"] = int(style["alignment"])
    if style.get("margin_v"):
        layout["margin_v"] = int(style["margin_v"])
    if style.get("margin_l") or style.get("margin_x"):
        layout["margin_x"] = int(style.get("margin_l") or style.get("margin_x"))
        layout["margin_l"] = layout["margin_x"]
    if style.get("margin_r"):
        layout["margin_r"] = int(style["margin_r"])
    else:
        layout["margin_r"] = layout.get("margin_l", layout["margin_x"])
    return layout


def claims_reference_aligned(style: dict[str, Any] | None) -> bool:
    if not style:
        return False
    note = str(style.get("note") or "")
    return "已对齐参考字幕" in note or (style.get("method") == "default_safe" and bool(style.get("aligned")))


def _subtitle_stream(source: Path) -> dict[str, Any] | None:
    result = subprocess.run(
        [
            media.ffprobe_bin(),
            "-v",
            "error",
            "-show_streams",
            "-of",
            "json",
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        info = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return None
    for stream in info.get("streams") or []:
        if stream.get("codec_type") == "subtitle":
            return stream
    return None


def _extract_subtitles(source: Path, dest: Path, fmt: str = "ass") -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = [media.ffmpeg_bin(), "-y", "-i", str(source), "-map", "0:s:0"]
    if fmt == "srt":
        args.extend(["-c:s", "srt"])
    args.append(str(dest))
    try:
        media.run_ffmpeg(args, timeout=30)
    except Exception:
        if dest.exists():
            dest.unlink()
        return False
    return dest.is_file() and dest.stat().st_size > 0


def _int_after(text: str, prefix: str) -> int:
    for line in text.splitlines():
        if line.strip().startswith(prefix):
            return _safe_int(line.split(":", 1)[1], 0)
    return 0


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default
