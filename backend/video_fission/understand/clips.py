"""把片段切到 artifacts，并准备模型输入文件。"""

from __future__ import annotations

import logging
from pathlib import Path

from video_fission import media

logger = logging.getLogger(__name__)

def cut_playback_clip(
    source: Path,
    dest: Path,
    start_ms: int,
    end_ms: int,
    width: int,
    height: int,
    fps: float,
    keep_audio: bool,
    timeout: int = 120,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    return media.cut_segment(source, dest, start_ms, end_ms, width, height, fps, keep_audio, timeout)


def ensure_under_size(source: Path, dest_dir: Path, max_mb: float = 15.0) -> tuple[Path, str]:
    """必要时压缩到模型体积上限。返回 (路径, warning)。"""
    limit = max(float(max_mb), 1.0) * 1024 * 1024
    size = source.stat().st_size
    if size <= limit:
        return source, ""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{source.stem}_gemini.mp4"
    # 估算码率：目标体积的约 80% 余量，至少 200kbps
    duration = max(media.probe(source)["duration_ms"] / 1000, 0.2)
    target_bitrate = max(int(limit * 0.8 * 8 / duration / 1000), 200)
    args = [
        media.ffmpeg_bin(),
        "-y",
        "-i",
        str(source),
        "-vf",
        media.limited_range_vf("scale=trunc(iw/2)*2:trunc(ih/2)*2"),
        *media.limited_x264_args(),
        "-b:v",
        f"{target_bitrate}k",
        "-maxrate",
        f"{int(target_bitrate * 1.2)}k",
        "-bufsize",
        f"{int(target_bitrate * 2)}k",
        "-c:a",
        "aac",
        "-ac",
        "2",
        "-ar",
        "44100",
        "-b:a",
        "64k",
        "-movflags",
        "+faststart",
        str(dest),
    ]
    try:
        media.run_ffmpeg(args, timeout=180)
    except Exception as exc:  # noqa: BLE001
        logger.info("压缩到模型体积上限失败: %s", exc)
        return source, f"视频超过 {max_mb:.0f}MB 且压缩失败，将改用关键帧"
    if dest.is_file() and dest.stat().st_size <= limit:
        return dest, ""
    warning = f"压缩后仍超过 {max_mb:.0f}MB，将改用关键帧"
    return dest if dest.is_file() else source, warning
