from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from video_fission.media import (
    burn_subtitles,
    default_font,
    ffmpeg_bin,
    probe,
    subtitle_burn_style,
)


def _supports_subtitle_burn() -> bool:
    result = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", "-filters"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and any(
        line.split()[1:2] == ["subtitles"] for line in result.stdout.splitlines() if line.split()
    )


pytestmark = pytest.mark.skipif(
    not _supports_subtitle_burn(),
    reason="ffmpeg was built without the subtitles/libass filter",
)


def test_subtitle_burn_style_scales_with_frame():
    style_16, size_16 = subtitle_burn_style(1280, 720)
    style_9, size_9 = subtitle_burn_style(1080, 1920)
    assert size_16 == "1280x720"
    assert size_9 == "1080x1920"
    assert "Alignment=2" in style_16
    assert "MarginV=144" in style_16  # 20% of 720
    assert "FontSize=32" in style_16  # 4.5% of 720
    assert "MarginV=384" in style_9  # 20% of 1920
    assert "FontSize=86" in style_9  # 4.5% of 1920


def _solid_clip(path: Path, width: int, height: int, duration: float = 2.0) -> None:
    result = subprocess.run(
        [
            ffmpeg_bin(),
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x1850A0:s={width}x{height}:d={duration}:r=25",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=stereo",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr[-1000:])


def _write_srt(path: Path, text: str = "开场要吵围观") -> None:
    path.write_text(
        f"1\n00:00:00,000 --> 00:00:02,000\n{text}\n",
        encoding="utf-8",
    )


def _frame_rgb(video: Path, width: int, height: int, at: float = 0.4) -> bytes:
    raw = video.with_suffix(".rgb")
    result = subprocess.run(
        [
            ffmpeg_bin(),
            "-y",
            "-ss",
            f"{at:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            str(raw),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr[-1000:])
    data = raw.read_bytes()
    expected = width * height * 3
    assert len(data) == expected
    return data


def _text_rows(data: bytes, width: int, height: int) -> list[int]:
    rows = []
    for y in range(height):
        bright = 0
        row = y * width * 3
        for x in range(width):
            i = row + x * 3
            if data[i] + data[i + 1] + data[i + 2] >= 500:
                bright += 1
        if bright >= max(8, width // 80):
            rows.append(y)
    return rows


def _assert_caption_clears_controls(video: Path, width: int, height: int) -> None:
    data = _frame_rgb(video, width, height)
    rows = _text_rows(data, width, height)
    assert rows, "burned caption was not found"
    lowest = rows[-1]
    highest = rows[0]
    control_zone = int(height * 0.12)
    assert lowest < height - control_zone, (
        f"caption bottom at y={lowest} sits in the control zone "
        f"(height={height}, reserved={control_zone})"
    )
    assert highest > height * 0.60, f"caption unexpectedly high (y={highest})"


def test_burn_subtitles_clears_control_bar_16x9(tmp_path: Path):
    default_font()
    src = tmp_path / "src.mp4"
    srt = tmp_path / "final.srt"
    dest = tmp_path / "burned.mp4"
    _solid_clip(src, 1280, 720)
    _write_srt(srt)
    burn_subtitles(src, srt, dest)
    info = probe(dest)
    assert info["width"] == 1280
    assert info["height"] == 720
    _assert_caption_clears_controls(dest, 1280, 720)


def test_burn_subtitles_clears_control_bar_9x16(tmp_path: Path):
    default_font()
    src = tmp_path / "src.mp4"
    srt = tmp_path / "final.srt"
    dest = tmp_path / "burned.mp4"
    _solid_clip(src, 1080, 1920)
    _write_srt(srt)
    burn_subtitles(src, srt, dest)
    info = probe(dest)
    assert info["width"] == 1080
    assert info["height"] == 1920
    _assert_caption_clears_controls(dest, 1080, 1920)
