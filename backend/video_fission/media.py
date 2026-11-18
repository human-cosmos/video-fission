from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .logging_utils import clip_text

logger = logging.getLogger(__name__)

FONT_CANDIDATES = [
    Path("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    Path("/System/Library/Fonts/STHeiti Medium.ttc"),
    Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
    Path("/System/Library/Fonts/HelveticaNeue.ttc"),
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("C:/Windows/Fonts/msyhbd.ttc"),
    Path("C:/Windows/Fonts/simhei.ttf"),
    Path("C:/Windows/Fonts/simsun.ttc"),
    Path("C:/Windows/Fonts/arial.ttf"),
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
    inputs = [args[index + 1] for index, value in enumerate(args[:-1]) if value == "-i"]
    output = args[-1] if args else "-"
    started = time.perf_counter()
    logger.info(
        "media.ffmpeg_started inputs=%s output=%s timeout_s=%s args=%s",
        ",".join(Path(value).name for value in inputs) or "-",
        Path(output).name,
        timeout,
        len(args),
    )
    logger.debug("media.ffmpeg_command command=%s", clip_text(args, 2400))
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.error(
            "media.ffmpeg_timeout output=%s timeout_s=%s elapsed_ms=%.1f",
            Path(output).name,
            timeout,
            (time.perf_counter() - started) * 1000,
        )
        raise
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        tail = detail[-2000:] if detail else "unknown ffmpeg error"
        logger.error(
            "media.ffmpeg_failed output=%s returncode=%s elapsed_ms=%.1f stderr=%s",
            Path(output).name,
            result.returncode,
            (time.perf_counter() - started) * 1000,
            clip_text(tail, 2000),
        )
        raise MediaError(f"ffmpeg 失败: {tail}")
    output_path = Path(output)
    try:
        output_size = output_path.stat().st_size
    except OSError:
        output_size = "-"
    logger.info(
        "media.ffmpeg_completed output=%s size=%s elapsed_ms=%.1f",
        output_path.name,
        output_size,
        (time.perf_counter() - started) * 1000,
    )
    return result


def probe(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise MediaError("视频文件不存在")
    started = time.perf_counter()
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
        logger.warning("media.probe_failed file=%s returncode=%s", path.name, result.returncode)
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
    info_result = {
        "duration_ms": int(round(duration * 1000)),
        "audio_duration_ms": int(round(audio_duration * 1000)) if audio else 0,
        "width": width,
        "height": height,
        "fps": fps,
        "has_audio": audio is not None,
        "audio_channels": int(audio.get("channels") or 1) if audio else 0,
        "pix_fmt": str(video.get("pix_fmt") or ""),
        "color_range": str(video.get("color_range") or ""),
    }
    logger.debug(
        "media.probe_completed file=%s duration_ms=%s resolution=%sx%s fps=%.3f audio=%s elapsed_ms=%.1f",
        path.name,
        info_result["duration_ms"],
        width,
        height,
        fps,
        audio is not None,
        (time.perf_counter() - started) * 1000,
    )
    return info_result


def probe_audio(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise MediaError("音频文件不存在")
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
        raise MediaError("无法解析音频文件")
    try:
        info = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise MediaError("ffprobe 输出无法解析") from exc
    audio = next((s for s in (info.get("streams") or []) if s.get("codec_type") == "audio"), None)
    if not audio:
        raise MediaError("文件中没有音频流")
    raw = audio.get("duration") or (info.get("format") or {}).get("duration") or 0
    try:
        duration = float(raw or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return {
        "duration_ms": int(round(duration * 1000)) if duration > 0 else 0,
        "channels": int(audio.get("channels") or 1),
        "has_audio": True,
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
    started = time.perf_counter()
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
    cuts = sorted(set(t for t in times if t > 0))
    logger.info(
        "media.scene_detection_completed file=%s threshold=%s cuts=%s elapsed_ms=%.1f",
        path.name,
        threshold,
        len(cuts),
        (time.perf_counter() - started) * 1000,
    )
    return cuts


def silence_points_ms(path: Path) -> list[int]:
    started = time.perf_counter()
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
    result_points = sorted(set(p for p in points if p > 0))
    logger.info(
        "media.silence_detection_completed file=%s points=%s elapsed_ms=%.1f",
        path.name,
        len(result_points),
        (time.perf_counter() - started) * 1000,
    )
    return result_points


def extract_frames(path: Path, timestamps_ms: list[int], output_dir: Path, prefix: str) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    logger.info("media.frame_extract_started file=%s timestamps=%s prefix=%s", path.name, timestamps_ms, prefix)
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
    logger.info(
        "media.frame_extract_completed file=%s requested=%s frames=%s elapsed_ms=%.1f",
        path.name,
        len(timestamps_ms),
        len(frames),
        (time.perf_counter() - started) * 1000,
    )
    return frames


def extract_audio(path: Path, dest: Path, timeout: int = 60) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    logger.info("media.audio_extract_started input=%s output=%s timeout_s=%s", path.name, dest.name, timeout)
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
    logger.info("media.audio_extract_completed output=%s size=%s", dest.name, dest.stat().st_size)
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
    logger.info(
        "media.cut_segment_started source=%s output=%s range=%s-%sms target=%sx%s fps=%.3f audio=%s",
        path.name,
        dest.name,
        start_ms,
        end_ms,
        width,
        height,
        fps,
        keep_audio,
    )
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
    logger.info("media.cut_segment_completed output=%s size=%s", dest.name, dest.stat().st_size)
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
    logger.info(
        "media.ken_burns_started image=%s output=%s duration_ms=%s target=%sx%s fps=%.3f text_chars=%s overlay_text=%s",
        image.name,
        dest.name,
        duration_ms,
        width,
        height,
        fps,
        len(text),
        overlay_text,
    )
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
    logger.info("media.ken_burns_completed output=%s size=%s", dest.name, dest.stat().st_size)
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
            "anullsrc=channel_layout=stereo:sample_rate=44100",
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
    logger.info(
        "media.audio_mux_started video=%s audio=%s output=%s audio_start_ms=%s duration_ms=%s",
        video.name,
        source.name,
        dest.name,
        start_ms,
        duration_ms,
    )
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
    logger.info("media.audio_mux_completed output=%s size=%s", dest.name, dest.stat().st_size)
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
    logger.info(
        "media.edge_fade_started input=%s output=%s duration_ms=%s fade_in=%s fade_out=%s",
        path.name,
        dest.name,
        duration_ms,
        fade_in,
        fade_out,
    )
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
    logger.info("media.edge_fade_completed output=%s size=%s", dest.name, dest.stat().st_size)
    return dest


def fit_av_duration(path: Path, dest: Path, duration_ms: int, fps: float) -> Path:
    """Force video and audio to the same exact duration (no one-frame tail)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    logger.info(
        "media.av_align_started input=%s output=%s target_ms=%s fps=%.3f",
        path.name,
        dest.name,
        duration_ms,
        fps,
    )
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
    logger.info("media.av_align_completed output=%s size=%s", dest.name, dest.stat().st_size)
    return dest


def normalize_video_clip(
    path: Path,
    dest: Path,
    duration_ms: int,
    width: int,
    height: int,
    fps: float,
) -> Path:
    """Normalize a remote clip to the project's exact visual contract."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    logger.info(
        "media.normalize_clip_started input=%s output=%s duration_ms=%s target=%sx%s fps=%.3f",
        path.name,
        dest.name,
        duration_ms,
        width,
        height,
        fps,
    )
    frames, duration = exact_seconds(duration_ms, fps)
    vf = limited_range_vf(
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={fps:.4f},tpad=stop_mode=clone:stop_duration={duration:.6f},"
        f"trim=end_frame={frames},setpts=PTS-STARTPTS"
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
        af = (
            f"atrim=0:{duration:.6f},asetpts=PTS-STARTPTS,"
            f"aformat=sample_rates=44100:channel_layouts=stereo,apad=whole_dur={duration:.6f}"
        )
        args.extend(["-af", af, "-c:a", "aac", "-ac", "2", "-ar", "44100"])
    else:
        args.append("-an")
    args.append(str(dest))
    run_ffmpeg(args, timeout=180)
    logger.info("media.normalize_clip_completed output=%s size=%s", dest.name, dest.stat().st_size)
    return dest


def concat_clips(clips: list[Path], dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    logger.info(
        "media.concat_started clips=%s output=%s inputs=%s",
        len(clips),
        dest.name,
        ",".join(item.name for item in clips),
    )
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
    logger.info("media.concat_completed output=%s size=%s", dest.name, dest.stat().st_size)
    return dest


def mix_bgm(video: Path, bgm: Path, dest: Path, bgm_volume: float, duration_ms: int) -> Path:
    duration = max(duration_ms, 1) / 1000
    volume = min(max(bgm_volume, 0.0), 1.0)
    logger.info(
        "media.bgm_mix_started video=%s bgm=%s output=%s volume=%.3f duration_ms=%s",
        video.name,
        bgm.name,
        dest.name,
        volume,
        duration_ms,
    )
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
    logger.info("media.bgm_mix_completed output=%s size=%s", dest.name, dest.stat().st_size)
    return dest


def subtitle_burn_style(width: int, height: int) -> tuple[str, str]:
    """ASS force_style and original_size so captions sit above player chrome.

    MarginV is 20% of the frame height so text clears a typical native
    control bar even when a tall 9:16 video is shown in a short player.
    PlayRes is the real frame size, so MarginV is in video pixels.
    """
    layout = subtitle_burn_layout(width, height)
    return layout["style"], layout["original_size"]


def subtitle_burn_layout(width: int, height: int) -> dict[str, Any]:
    width = max(int(width), 2)
    height = max(int(height), 2)
    font_size = max(28, round(height * 0.045))
    margin_v = max(96, round(height * 0.20))
    margin_x = max(24, round(width * 0.05))
    style = (
        "FontName=Droid Sans Fallback,"
        f"FontSize={font_size},"
        "PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00111111,"
        "BorderStyle=1,"
        "Outline=2,"
        "Shadow=1,"
        "Alignment=2,"
        f"MarginV={margin_v}"
    )
    return {
        "width": width,
        "height": height,
        "font_size": font_size,
        "margin_v": margin_v,
        "margin_x": margin_x,
        "style": style,
        "original_size": f"{width}x{height}",
    }


def _ass_timestamp(srt_ts: str) -> str:
    stamp = srt_ts.strip().replace(",", ".")
    parts = stamp.split(":")
    if len(parts) != 3:
        raise MediaError(f"无法解析字幕时间戳：{srt_ts}")
    hours, minutes, rest = parts
    seconds, _, frac = rest.partition(".")
    millis = int((frac + "000")[:3]) if frac else 0
    centis = min(99, int(round(millis / 10)))
    return f"{int(hours)}:{int(minutes):02d}:{int(seconds):02d}.{centis:02d}"


def _parse_srt_cues(path: Path) -> list[tuple[str, str, str]]:
    blocks = re.split(r"\n\s*\n", path.read_text(encoding="utf-8").strip())
    cues: list[tuple[str, str, str]] = []
    for block in blocks:
        lines = [line.rstrip() for line in block.splitlines()]
        time_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if time_index is None:
            continue
        start_raw, end_raw = [part.strip() for part in lines[time_index].split("-->")]
        text = "\\N".join(line.strip() for line in lines[time_index + 1:] if line.strip())
        text = text.replace("{", "\\{").replace("}", "\\}")
        if text:
            cues.append((_ass_timestamp(start_raw), _ass_timestamp(end_raw), text))
    return cues


def layout_with_style(width: int, height: int, style: dict[str, Any] | None = None) -> dict[str, Any]:
    layout = subtitle_burn_layout(width, height)
    layout["font_name"] = "Droid Sans Fallback"
    layout["primary_colour"] = "&H00FFFFFF"
    layout["outline_colour"] = "&H00111111"
    layout["alignment"] = 2
    layout["margin_l"] = layout["margin_x"]
    layout["margin_r"] = layout["margin_x"]
    if style and style.get("method") != "default_safe" and style.get("aligned"):
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
            layout["margin_r"] = layout["margin_l"]
    return layout


def write_ass_from_srt(
    srt: Path,
    dest: Path,
    width: int,
    height: int,
    style: dict[str, Any] | None = None,
) -> Path:
    layout = layout_with_style(width, height, style)
    dest.parent.mkdir(parents=True, exist_ok=True)
    events = []
    for start, end, text in _parse_srt_cues(srt):
        events.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")
    body = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {layout['width']}\n"
        f"PlayResY: {layout['height']}\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "YCbCr Matrix: TV.709\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{layout.get('font_name', 'Droid Sans Fallback')},"
        f"{layout['font_size']},{layout.get('primary_colour', '&H00FFFFFF')},"
        f"&H000000FF,{layout.get('outline_colour', '&H00111111')},&H80000000,"
        f"0,0,0,0,100,100,0,0,1,2,1,{layout.get('alignment', 2)},"
        f"{layout.get('margin_l', layout['margin_x'])},"
        f"{layout.get('margin_r', layout['margin_x'])},"
        f"{layout['margin_v']},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        + "\n".join(events)
        + ("\n" if events else "")
    )
    dest.write_text(body, encoding="utf-8")
    return dest


def burn_subtitles(video: Path, srt: Path, dest: Path, style: dict[str, Any] | None = None) -> Path:
    logger.info(
        "media.subtitle_burn_started video=%s srt=%s output=%s style=%s",
        video.name,
        srt.name,
        dest.name,
        (style or {}).get("method") or "-",
    )
    font = default_font()
    fonts_dir = str(font.parent).replace("\\", "\\\\").replace(":", "\\:")
    info = probe(video)
    ass_path = dest.parent / f"{dest.stem}.ass"
    write_ass_from_srt(srt, ass_path, info["width"], info["height"], style)
    ass_arg = str(ass_path.resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    run_ffmpeg(
        [
            ffmpeg_bin(),
            "-y",
            "-i",
            str(video),
            "-vf",
            limited_range_vf(
                f"subtitles='{ass_arg}':fontsdir='{fonts_dir}'"
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
    logger.info("media.subtitle_burn_completed output=%s size=%s", dest.name, dest.stat().st_size)
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
