from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

FONT_CANDIDATES = [
    Path("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
]


class MediaError(ValueError):
    pass


def ffmpeg_bin() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise MediaError("未找到 ffmpeg")
    return path


def ffprobe_bin() -> str:
    path = shutil.which("ffprobe")
    if not path:
        raise MediaError("未找到 ffprobe")
    return path


def ffmpeg_available() -> dict[str, Any]:
    try:
        ff = ffmpeg_bin()
        fp = ffprobe_bin()
    except MediaError as exc:
        return {"ok": False, "detail": str(exc)}
    try:
        version = subprocess.run(
            [ff, "-version"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        first = (version.stdout or version.stderr).splitlines()[0] if (version.stdout or version.stderr) else ""
        return {"ok": version.returncode == 0, "ffmpeg": ff, "ffprobe": fp, "version": first}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "detail": str(exc)}


def default_font() -> Path:
    for path in FONT_CANDIDATES:
        if path.is_file():
            return path
    raise MediaError("未找到可用字体，无法烧录字幕或叠字")


def escape_drawtext(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("'", "’")
        .replace(":", "\\:")
        .replace("%", "%%")
        .replace("\n", "\\n")
    )



def limited_range_vf(vf: str = "") -> str:
    """Force limited-range yuv420p in the filter graph (JPEG/full-range sources included)."""
    extra = "scale=out_range=tv,format=yuv420p,setsar=1"
    vf = (vf or "").rstrip(",")
    return f"{vf},{extra}" if vf else extra


def limited_x264_args() -> list[str]:
    return [
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-color_range",
        "tv",
        "-colorspace",
        "bt709",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
    ]


def run_ffmpeg(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        tail = detail[-2000:] if detail else "unknown ffmpeg error"
        raise MediaError(f"ffmpeg 失败: {tail}")
    return result


def probe(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise MediaError("视频文件不存在")
    result = subprocess.run(
        [
            ffprobe_bin(),
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise MediaError("无法解析视频，请确认文件是可播放的 mp4/mov/webm")
    try:
        info = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise MediaError("ffprobe 输出无法解析") from exc
    streams = info.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video:
        raise MediaError("文件中没有视频流")
    duration = 0.0
    raw_duration = video.get("duration") or (info.get("format") or {}).get("duration")
    if raw_duration:
        try:
            duration = float(raw_duration)
        except (TypeError, ValueError):
            duration = 0.0
    if duration <= 0:
        raise MediaError("无法读取视频时长")
    fps = _parse_fps(video.get("r_frame_rate") or video.get("avg_frame_rate") or "30/1")
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if width <= 0 or height <= 0:
        raise MediaError("无法读取视频尺寸")
    audio_duration = 0.0
    if audio:
        raw_audio = audio.get("duration") or raw_duration
        try:
            audio_duration = float(raw_audio or 0)
        except (TypeError, ValueError):
            audio_duration = 0.0
    return {
        "duration_ms": int(round(duration * 1000)),
        "audio_duration_ms": int(round(audio_duration * 1000)) if audio else 0,
        "width": width,
        "height": height,
        "fps": fps,
        "has_audio": audio is not None,
        "pix_fmt": str(video.get("pix_fmt") or ""),
        "color_range": str(video.get("color_range") or ""),
    }


def exact_seconds(duration_ms: int, fps: float) -> tuple[int, float]:
    rate = fps if fps and fps > 0 else 30.0
    frames = max(1, int(round(max(duration_ms, 1) * rate / 1000.0)))
    return frames, frames / rate


def audio_levels(path: Path) -> dict[str, float]:
    if not path.is_file():
        raise MediaError("视频文件不存在")
    result = subprocess.run(
        [
            ffmpeg_bin(),
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    blob = (result.stderr or "") + "\n" + (result.stdout or "")
    mean = re.search(r"mean_volume:\s*(-?[0-9.]+)\s*dB", blob)
    peak = re.search(r"max_volume:\s*(-?[0-9.]+)\s*dB", blob)
    if not mean or not peak:
        raise MediaError("无法读取音量")
    return {"mean_db": float(mean.group(1)), "max_db": float(peak.group(1))}


def _parse_fps(value: str) -> float:
    try:
        if "/" in str(value):
            num, den = str(value).split("/", 1)
            den_f = float(den)
            return float(num) / den_f if den_f else 30.0
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return 30.0


def scene_cut_ms(path: Path, threshold: float = 0.15) -> list[int]:
    result = subprocess.run(
        [
            ffmpeg_bin(),
            "-hide_banner",
            "-i",
            str(path),
            "-filter:v",
            f"select='gt(scene,{threshold})',showinfo",
            "-an",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    times: list[int] = []
    for match in re.finditer(r"pts_time:([0-9.]+)", result.stderr or ""):
        times.append(int(round(float(match.group(1)) * 1000)))
    return sorted(set(t for t in times if t > 0))


def silence_points_ms(path: Path) -> list[int]:
    result = subprocess.run(
        [
            ffmpeg_bin(),
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            "silencedetect=noise=-30dB:d=0.18",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    points: list[int] = []
    for match in re.finditer(r"silence_(?:start|end):\s*([0-9.]+)", result.stderr or ""):
        points.append(int(round(float(match.group(1)) * 1000)))
    return sorted(set(p for p in points if p > 0))


def extract_frames(path: Path, timestamps_ms: list[int], output_dir: Path, prefix: str) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    frames: list[Path] = []
    for index, ts in enumerate(timestamps_ms, start=1):
        dest = output_dir / f"{prefix}_{index:02d}.jpg"
        seconds = max(ts, 0) / 1000
        run_ffmpeg(
            [
                ffmpeg_bin(),
                "-y",
                "-ss",
                f"{seconds:.3f}",
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-q:v",
                "3",
                str(dest),
            ],
            timeout=30,
        )
        if dest.is_file() and dest.stat().st_size > 0:
            frames.append(dest)
    return frames


def extract_audio(path: Path, dest: Path, timeout: int = 60) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            ffmpeg_bin(),
            "-y",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "2",
            "-ar",
            "44100",
            "-c:a",
            "aac",
            str(dest),
        ],
        timeout=timeout,
    )
    return dest


def cut_segment(
    path: Path,
    dest: Path,
    start_ms: int,
    end_ms: int,
    width: int,
    height: int,
    fps: float,
    keep_audio: bool,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    duration = max(end_ms - start_ms, 1) / 1000
    args = [
        ffmpeg_bin(),
        "-y",
        "-ss",
        f"{start_ms / 1000:.3f}",
        "-i",
        str(path),
        "-t",
        f"{duration:.3f}",
        "-vf",
        limited_range_vf(
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps:.4f}"
        ),
        *limited_x264_args(),
        "-movflags",
        "+faststart",
    ]
    if keep_audio:
        args.extend(["-c:a", "aac", "-ac", "2", "-ar", "44100"])
    else:
        args.extend(["-an"])
    args.append(str(dest))
    run_ffmpeg(args, timeout=120)
    return dest


def ken_burns_clip(
    image: Path,
    dest: Path,
    duration_ms: int,
    width: int,
    height: int,
    fps: float,
    text: str = "",
    overlay_text: bool = True,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    _frames, duration = exact_seconds(max(duration_ms, 200), fps)
    # Slow pan + zoom. Skip drawtext when overlay_text is off so burned
    # subtitles do not stack on top of the same words.
    vf = (
        f"scale={int(width * 1.18)}:{int(height * 1.18)},"
        f"crop={width}:{height}:x='(in_w-out_w)*t/{duration:.4f}':"
        f"y='(in_h-out_h)*t/{max(duration * 1.15, 0.2):.4f}',"
        f"fps={fps:.4f}"
    )
    label = escape_drawtext((text or "").strip()[:24])
    if overlay_text and label:
        font = default_font()
        vf += (
            f",drawtext=fontfile='{font}':text='{label}':fontsize="
            f"{max(int(height * 0.06), 28)}:fontcolor=white:"
            "borderw=2:bordercolor=black@0.6:x=(w-text_w)/2:y=h*0.78"
        )
    run_ffmpeg(
        [
            ffmpeg_bin(),
            "-y",
            "-loop",
            "1",
            "-i",
            str(image),
            "-t",
            f"{duration:.6f}",
            "-vf",
            limited_range_vf(vf),
            *limited_x264_args(),
            "-an",
            "-movflags",
            "+faststart",
            str(dest),
        ],
        timeout=120,
    )
    return dest


def add_silence_audio(video: Path, dest: Path, duration_ms: int) -> Path:
    duration = max(duration_ms, 1) / 1000
    run_ffmpeg(
        [
            ffmpeg_bin(),
            "-y",
            "-i",
            str(video),
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=channel_layout=stereo:sample_rate=44100",
            "-t",
            f"{duration:.3f}",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            "-movflags",
            "+faststart",
            str(dest),
        ],
        timeout=60,
    )
    return dest


def mux_timeline_audio(
    video: Path,
    source: Path,
    start_ms: int,
    dest: Path,
    duration_ms: int,
    fade_in_ms: int = 0,
    fade_out_ms: int = 0,
) -> Path:
    """Lay the source timeline audio for this window onto a generated clip."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    _frames, duration = exact_seconds(duration_ms, probe(video).get("fps") or 30)
    start = max(start_ms, 0) / 1000
    fades: list[str] = []
    if fade_in_ms > 0:
        fades.append(f"afade=t=in:st=0:d={fade_in_ms / 1000:.3f}")
    if fade_out_ms > 0:
        fade_start = max(duration - fade_out_ms / 1000, 0)
        fades.append(f"afade=t=out:st={fade_start:.3f}:d={fade_out_ms / 1000:.3f}")
    fade = (",".join(fades) + ",") if fades else ""
    run_ffmpeg(
        [
            ffmpeg_bin(),
            "-y",
            "-i",
            str(video),
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{duration:.6f}",
            "-i",
            str(source),
            "-filter_complex",
            f"[0:v]trim=0:{duration:.6f},setpts=PTS-STARTPTS[v];"
            f"[1:a]atrim=0:{duration:.6f},asetpts=PTS-STARTPTS,"
            f"aformat=sample_rates=44100:channel_layouts=stereo,{fade}"
            f"apad=whole_dur={duration:.6f}[a]",
            "-map",
            "[v]",
            "-map",
            "[a]",
            *limited_x264_args(),
            "-c:a",
            "aac",
            "-ac",
            "2",
            "-ar",
            "44100",
            "-t",
            f"{duration:.6f}",
            "-movflags",
            "+faststart",
            str(dest),
        ],
        timeout=120,
    )
    return dest


def apply_edge_fades(
    path: Path,
    dest: Path,
    duration_ms: int,
    fps: float,
    fade_in: bool,
    fade_out: bool,
    video_fade_frames: int = 10,
    audio_fade_ms: int = 100,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    _frames, duration = exact_seconds(duration_ms, fps)
    max_each = max(duration / 3, 0.04)
    video_d = min(max(video_fade_frames, 8) / max(fps, 1), max_each)
    audio_d = min(max(audio_fade_ms, 80) / 1000, max_each)
    vf_parts: list[str] = [f"trim=0:{duration:.6f}", "setpts=PTS-STARTPTS", f"fps={fps:.4f}"]
    if fade_in:
        vf_parts.append(f"fade=t=in:st=0:d={video_d:.3f}")
    if fade_out:
        vf_parts.append(f"fade=t=out:st={max(duration - video_d, 0):.3f}:d={video_d:.3f}")
    af_parts = [
        f"atrim=0:{duration:.6f}",
        "asetpts=PTS-STARTPTS",
        "aformat=sample_rates=44100:channel_layouts=stereo",
    ]
    if fade_in:
        af_parts.append(f"afade=t=in:st=0:d={audio_d:.3f}")
    if fade_out:
        af_parts.append(f"afade=t=out:st={max(duration - audio_d, 0):.3f}:d={audio_d:.3f}")
    args = [
        ffmpeg_bin(),
        "-y",
        "-i",
        str(path),
        "-vf",
        limited_range_vf(",".join(vf_parts)),
        *limited_x264_args(),
        "-movflags",
        "+faststart",
        "-t",
        f"{duration:.6f}",
    ]
    if probe(path).get("has_audio"):
        args.extend(["-af", ",".join(af_parts), "-c:a", "aac", "-ac", "2", "-ar", "44100"])
    else:
        args.append("-an")
    args.append(str(dest))
    run_ffmpeg(args, timeout=120)
    return dest


def fit_av_duration(path: Path, dest: Path, duration_ms: int, fps: float) -> Path:
    """Force video and audio to the same exact duration (no one-frame tail)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    frames, duration = exact_seconds(duration_ms, fps)
    vf = limited_range_vf(
        f"fps={fps:.4f},tpad=stop_mode=clone:stop_duration={duration:.6f},"
        f"trim=end_frame={frames},setpts=PTS-STARTPTS"
    )
    af = (
        f"atrim=0:{duration:.6f},asetpts=PTS-STARTPTS,"
        f"aformat=sample_rates=44100:channel_layouts=stereo,apad=whole_dur={duration:.6f}"
    )
    args = [
        ffmpeg_bin(),
        "-y",
        "-i",
        str(path),
        "-vf",
        vf,
        *limited_x264_args(),
        "-r",
        f"{fps:.4f}",
        "-t",
        f"{duration:.6f}",
        "-movflags",
        "+faststart",
    ]
    if abs(fps - int(round(fps))) < 0.001:
        args.extend(["-video_track_timescale", str(int(round(fps)))])
    if probe(path).get("has_audio"):
        args.extend(["-af", af, "-c:a", "aac", "-ac", "2", "-ar", "44100"])
    else:
        args.append("-an")
    args.append(str(dest))
    run_ffmpeg(args, timeout=180)
    return dest


def concat_clips(clips: list[Path], dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    listing = dest.with_suffix(".concat.txt")
    listing.write_text("".join(f"file '{path.resolve()}'\n" for path in clips), encoding="utf-8")
    run_ffmpeg(
        [
            ffmpeg_bin(),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(listing),
            "-vf",
            limited_range_vf(),
            *limited_x264_args(),
            "-c:a",
            "aac",
            "-ac",
            "2",
            "-ar",
            "44100",
            "-movflags",
            "+faststart",
            str(dest),
        ],
        timeout=180,
    )
    return dest


def mix_bgm(video: Path, bgm: Path, dest: Path, bgm_volume: float, duration_ms: int) -> Path:
    duration = max(duration_ms, 1) / 1000
    volume = min(max(bgm_volume, 0.0), 1.0)
    run_ffmpeg(
        [
            ffmpeg_bin(),
            "-y",
            "-i",
            str(video),
            "-stream_loop",
            "-1",
            "-i",
            str(bgm),
            "-t",
            f"{duration:.3f}",
            "-filter_complex",
            f"[1:a]volume={volume:.3f},atrim=0:{duration:.3f},asetpts=PTS-STARTPTS[bg];"
            f"[0:a]aformat=sample_rates=44100:channel_layouts=stereo[main];"
            f"[main][bg]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]",
            "-map",
            "0:v:0",
            "-map",
            "[a]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(dest),
        ],
        timeout=120,
    )
    return dest


def burn_subtitles(video: Path, srt: Path, dest: Path) -> Path:
    font = default_font()
    fonts_dir = str(font.parent).replace("\\", "\\\\").replace(":", "\\:")
    srt_path = str(srt.resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    style = "FontName=Droid Sans Fallback,FontSize=18,PrimaryColour=&H00FFFFFF,Outline=1"
    run_ffmpeg(
        [
            ffmpeg_bin(),
            "-y",
            "-i",
            str(video),
            "-vf",
            limited_range_vf(
                f"subtitles='{srt_path}':fontsdir='{fonts_dir}':force_style='{style}'"
            ),
            *limited_x264_args(),
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(dest),
        ],
        timeout=180,
    )
    return dest


def target_dimensions(width: int, height: int) -> tuple[int, int]:
    if height > width:
        if height > 1920:
            scale = 1920 / height
            width = int(round(width * scale))
            height = 1920
        # Even numbers for yuv420p
        return max(width - width % 2, 2), max(height - height % 2, 2)
    longest = max(width, height)
    if longest > 1920:
        scale = 1920 / longest
        width = int(round(width * scale))
        height = int(round(height * scale))
    return max(width - width % 2, 2), max(height - height % 2, 2)
