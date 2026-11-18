from __future__ import annotations

import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from . import media

logger = logging.getLogger(__name__)


def _has_demucs() -> bool:
    return bool(shutil.which("demucs"))


def separate_reference_audio(source: Path, dest_dir: Path) -> dict[str, Any]:
    """Split reference audio into vocals and BGM. Never copy the mix twice."""
    started = time.perf_counter()
    dest_dir.mkdir(parents=True, exist_ok=True)
    vocals = dest_dir / "reference-vocals.m4a"
    bgm = dest_dir / "reference-bgm.m4a"
    info = media.probe(source)
    logger.info(
        "audio.separation_started source=%s duration_ms=%s channels=%s demucs=%s",
        source.name,
        info.get("duration_ms"),
        info.get("audio_channels"),
        _has_demucs(),
    )
    if not info.get("has_audio"):
        _silent_pair(vocals, bgm, int(info.get("duration_ms") or 1000))
        logger.warning("audio.separation_completed method=placeholder reason=no_audio")
        return {
            "vocals_path": str(vocals),
            "bgm_path": str(bgm),
            "method": "placeholder",
            "usable": False,
            "warning": "参考视频没有音轨，已写出空的人声/BGM 占位，不能当作已分离口播",
        }
    if _has_demucs():
        try:
            _separate_demucs(source, vocals, bgm)
            _assert_distinct(vocals, bgm, source)
            result = {
                "vocals_path": str(vocals),
                "bgm_path": str(bgm),
                "method": "demucs",
                "usable": True,
                "warning": "",
            }
            logger.info(
                "audio.separation_completed method=demucs vocals=%s bgm=%s elapsed_ms=%.1f",
                vocals.stat().st_size,
                bgm.stat().st_size,
                (time.perf_counter() - started) * 1000,
            )
            return result
        except Exception as exc:
            logger.warning("audio.demucs_failed fallback=local_ms", exc_info=True)
            warning = f"Demucs 分离失败，已改用本地中置/侧边：{exc.__class__.__name__}"
            _separate_local_ms(source, vocals, bgm)
            _assert_distinct(vocals, bgm, source)
            result = {
                "vocals_path": str(vocals),
                "bgm_path": str(bgm),
                "method": "local_ms",
                "usable": True,
                "warning": warning,
            }
            logger.info(
                "audio.separation_completed method=local_ms fallback=true elapsed_ms=%.1f",
                (time.perf_counter() - started) * 1000,
            )
            return result
    _separate_local_ms(source, vocals, bgm)
    _assert_distinct(vocals, bgm, source)
    result = {
        "vocals_path": str(vocals),
        "bgm_path": str(bgm),
        "method": "local_ms",
        "usable": True,
        "warning": "",
    }
    logger.info("audio.separation_completed method=local_ms elapsed_ms=%.1f", (time.perf_counter() - started) * 1000)
    return result


def _separate_demucs(source: Path, vocals: Path, bgm: Path) -> None:
    work = vocals.parent / "demucs-work"
    work.mkdir(parents=True, exist_ok=True)
    wav = work / "source.wav"
    media.run_ffmpeg(
        [media.ffmpeg_bin(), "-y", "-i", str(source), "-vn", "-ac", "2", "-ar", "44100", str(wav)],
        timeout=60,
    )
    started = time.perf_counter()
    logger.info("audio.demucs_started input=%s output_dir=%s timeout_s=180", wav.name, work)
    result = subprocess.run(
        ["demucs", "--two-stems", "vocals", "-o", str(work), str(wav)],
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        logger.error("audio.demucs_failed returncode=%s stderr=%s", result.returncode, detail[-2000:])
        raise RuntimeError(f"Demucs 执行失败：{detail[-500:]}")
    logger.info("audio.demucs_completed elapsed_ms=%.1f", (time.perf_counter() - started) * 1000)
    found_vocals = next(work.rglob("vocals.wav"), None)
    found_bgm = next(work.rglob("no_vocals.wav"), None) or next(work.rglob("accompaniment.wav"), None)
    if not found_vocals or not found_bgm:
        raise RuntimeError("Demucs 未产出人声/伴奏文件")
    _encode_m4a(found_vocals, vocals)
    _encode_m4a(found_bgm, bgm)


def _separate_local_ms(source: Path, vocals: Path, bgm: Path) -> None:
    info = media.probe(source)
    channels = int(info.get("audio_channels") or info.get("channels") or 1)
    if channels >= 2:
        vocals_filter = "pan=mono|c0=0.5*c0+0.5*c1,aformat=sample_fmts=fltp:sample_rates=44100"
        bgm_filter = "pan=mono|c0=0.5*c0-0.5*c1,aformat=sample_fmts=fltp:sample_rates=44100"
    else:
        # Mono: complementary bands so the two files are not byte-identical copies.
        vocals_filter = "highpass=f=200,lowpass=f=4000,aformat=sample_fmts=fltp:sample_rates=44100"
        bgm_filter = (
            "lowpass=f=180,highpass=f=4500,amix=inputs=2:dropout_transition=0,"
            "aformat=sample_fmts=fltp:sample_rates=44100"
        )
    _ffmpeg_audio(source, vocals, vocals_filter)
    if channels >= 2:
        _ffmpeg_audio(source, bgm, bgm_filter)
    else:
        # Split lows and highs into BGM with two input pads from the same source.
        media.run_ffmpeg(
            [
                media.ffmpeg_bin(),
                "-y",
                "-i",
                str(source),
                "-filter_complex",
                "[0:a]lowpass=f=180[low];[0:a]highpass=f=4500[high];[low][high]amix=inputs=2:dropout_transition=0,aformat=sample_fmts=fltp:sample_rates=44100",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "44100",
                "-c:a",
                "aac",
                str(bgm),
            ],
            timeout=60,
        )


def _ffmpeg_audio(source: Path, dest: Path, af: str) -> None:
    media.run_ffmpeg(
        [
            media.ffmpeg_bin(),
            "-y",
            "-i",
            str(source),
            "-vn",
            "-af",
            af,
            "-ac",
            "1",
            "-ar",
            "44100",
            "-c:a",
            "aac",
            str(dest),
        ],
        timeout=60,
    )


def _encode_m4a(source: Path, dest: Path) -> None:
    media.run_ffmpeg(
        [
            media.ffmpeg_bin(),
            "-y",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "2",
            "-ar",
            "44100",
            "-c:a",
            "aac",
            str(dest),
        ],
        timeout=60,
    )


def _silent_pair(vocals: Path, bgm: Path, duration_ms: int) -> None:
    duration = max(duration_ms, 1) / 1000
    for index, dest in enumerate((vocals, bgm)):
        # Slightly different frequencies so the files are not byte-identical.
        freq = 20 + index
        media.run_ffmpeg(
            [
                media.ffmpeg_bin(),
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={freq}:sample_rate=44100:duration={duration:.3f}",
                "-af",
                "volume=0.0001",
                "-ac",
                "1",
                "-c:a",
                "aac",
                str(dest),
            ],
            timeout=30,
        )


def _assert_distinct(vocals: Path, bgm: Path, source: Path) -> None:
    if not vocals.is_file() or not bgm.is_file():
        raise RuntimeError("人声或 BGM 文件未生成")
    if vocals.read_bytes() == bgm.read_bytes():
        raise RuntimeError("人声与 BGM 产物相同，分离无效")
    try:
        mix = source.parent / "_mix_check.m4a"
        media.extract_audio(source, mix)
        mix_bytes = mix.read_bytes()
        if vocals.read_bytes() == mix_bytes or bgm.read_bytes() == mix_bytes:
            raise RuntimeError("分离结果与原混音相同，分离无效")
    except Exception as exc:
        if "分离" in str(exc):
            raise
