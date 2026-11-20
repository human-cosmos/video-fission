#!/usr/bin/env python3
"""Run a real Seedance reference-image and reference-audio verification."""

from __future__ import annotations

import argparse
import base64
import json
import logging
import subprocess
import sys
import tempfile
import time
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from video_fission import providers  # noqa: E402
from video_fission.config import Settings  # noqa: E402
from video_fission.db import Database  # noqa: E402
from video_fission.media import ffmpeg_bin  # noqa: E402
from video_fission.settings_store import SettingsStore  # noqa: E402

IMAGE_PROMPT = (
    "参考图中的产品是本视频唯一主体。保持参考图中产品的轮廓、主色、把手颜色、"
    "表面图案和数量完全一致，白色干净背景，固定机位，产品只做轻微旋转和反光变化。"
    "不要改变产品颜色，不要增加其他产品，不要生成字幕、文字、标题或水印。"
)

AUDIO_PROMPT = (
    "参考图只用于锁定画面主体外观。使用输入内容中的 reference_audio 作为完整音轨，"
    "严格保留参考音频的音高序列、节拍、停顿和时长，不生成额外音乐、口播或环境声。"
    "背景保持干净，不生成字幕、文字或水印。"
)

REFERENCE_FREQUENCIES = (523.251, 1046.502, 1567.982, 392.0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", default="", help="Use this user's model settings.")
    parser.add_argument("--model", default="", help="Override the configured Seedance model.")
    parser.add_argument("--duration-seconds", type=int, default=4)
    parser.add_argument("--resolution", choices=("480p", "720p"), default="480p")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--skip-images", action="store_true")
    parser.add_argument("--skip-audio", action="store_true")
    return parser.parse_args()


def _pick_user(db: Database, requested: str) -> str:
    users = db.list_users()
    if requested:
        if not any(str(item.get("id")) == requested for item in users):
            raise RuntimeError("指定用户不存在")
        return requested
    admin = next((item for item in users if item.get("role") == "admin"), None)
    selected = admin or (users[0] if users else None)
    if not selected:
        raise RuntimeError("数据库中没有可用用户")
    return str(selected["id"])


def _resolve_model_settings(args: argparse.Namespace) -> dict[str, Any]:
    settings = Settings.from_env()
    db = Database(settings.database_path)
    store = SettingsStore(db, settings)
    user_id = _pick_user(db, args.user_id)
    public, secrets = store.raw(user_id)
    video = dict(public.get("video_generator") or {})
    base_url = str(video.get("base_url") or "").strip()
    api_key = str(secrets.get("video_generator") or "").strip()
    model = str(args.model or video.get("model") or "seedance-2-0-fast").strip()
    if str(video.get("provider") or "") != "seedance":
        raise RuntimeError(f"当前视频 Provider 不是 seedance: {video.get('provider')}")
    if not base_url:
        raise RuntimeError("Seedance base_url 未配置")
    if not api_key:
        raise RuntimeError("Seedance API Key 未配置")
    return {
        "user_id": user_id,
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "source": str(video.get("source") or "video-fission-reference-verification"),
        "timeout_seconds": int(video.get("timeout_seconds") or args.timeout),
    }


def _draw_product(
    path: Path,
    body_bgr: tuple[int, int, int],
    accent_bgr: tuple[int, int, int],
    marker: str,
) -> None:
    image = np.full((1280, 720, 3), 255, dtype=np.uint8)
    cv2.ellipse(image, (360, 690), (235, 80), 0, 0, 360, (225, 228, 232), -1)
    cv2.rectangle(image, (215, 300), (505, 900), body_bgr, -1)
    cv2.ellipse(image, (215, 600), (78, 300), 0, 90, 270, body_bgr, 60)
    cv2.ellipse(image, (505, 600), (145, 210), 0, 270, 90, accent_bgr, 42)
    cv2.rectangle(image, (215, 300), (505, 365), accent_bgr, -1)
    if marker == "A":
        for y in (500, 630, 760):
            for x in (295, 425):
                cv2.circle(image, (x, y), 24, (255, 255, 255), -1)
    else:
        for y in (500, 630, 760):
            for x in (295, 425):
                cv2.rectangle(image, (x - 24, y - 24), (x + 24, y + 24), (18, 18, 18), -1)
    cv2.imwrite(str(path), image, [int(cv2.IMWRITE_JPEG_QUALITY), 95])


def _create_reference_images(output_dir: Path) -> dict[str, Path]:
    image_a = output_dir / "reference_product_a.jpg"
    image_b = output_dir / "reference_product_b.jpg"
    _draw_product(image_a, (105, 28, 190), (20, 200, 235), "A")
    _draw_product(image_b, (155, 150, 10), (0, 115, 255), "B")
    return {"a": image_a, "b": image_b}


def _create_reference_audio(output_dir: Path) -> tuple[Path, Path]:
    sample_rate = 44_100
    duration = 4.0
    samples = np.zeros(int(sample_rate * duration), dtype=np.float32)
    schedule = (
        (0.00, 0.00, 0.75, REFERENCE_FREQUENCIES[0]),
        (1.00, 0.00, 0.75, REFERENCE_FREQUENCIES[1]),
        (2.00, 0.00, 0.75, REFERENCE_FREQUENCIES[2]),
        (3.00, 0.00, 1.00, REFERENCE_FREQUENCIES[3]),
    )
    for start, offset, length, frequency in schedule:
        begin = int((start + offset) * sample_rate)
        end = min(int((start + offset + length) * sample_rate), samples.size)
        t = np.arange(end - begin, dtype=np.float32) / sample_rate
        envelope = np.minimum(1.0, np.minimum(t / 0.02, (length - t) / 0.04))
        envelope = np.clip(envelope, 0.0, 1.0)
        samples[begin:end] += (
            0.32 * np.sin(2 * np.pi * frequency * t)
            + 0.08 * np.sin(2 * np.pi * frequency * 2 * t)
        ) * envelope
    pcm = np.clip(samples, -1.0, 1.0)
    pcm16 = (pcm * 32767).astype(np.int16)
    wav_path = output_dir / "reference_audio.wav"
    with wave.open(str(wav_path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm16.tobytes())
    m4a_path = output_dir / "reference_audio.m4a"
    subprocess.run(
        [
            ffmpeg_bin(),
            "-y",
            "-i",
            str(wav_path),
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            str(m4a_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return wav_path, m4a_path


def _payload_media_summary(payload: dict[str, Any]) -> dict[str, Any]:
    content = payload.get("input_contents") or []
    return {
        "model": payload.get("model"),
        "duration": payload.get("duration"),
        "aspect_ratio": payload.get("aspect_ratio"),
        "resolution": (payload.get("options") or {}).get("resolution"),
        "generate_audio": (payload.get("options") or {}).get("generate_audio"),
        "input_contents": [
            {"type": item.get("type"), "role": item.get("role")}
            for item in content
            if isinstance(item, dict)
        ],
    }


def _run_project_image_job(
    name: str,
    image_path: Path,
    output_dir: Path,
    config: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    dest = output_dir / f"{name}.mp4"
    payload = providers.build_seedance_submit_payload(
        config["model"],
        IMAGE_PROMPT,
        args.duration_seconds * 1000,
        720,
        1280,
        source=config["source"],
        resolution=args.resolution,
        reference_images=[image_path],
    )
    task_id = ""

    def submitted(value: str) -> None:
        nonlocal task_id
        task_id = value

    started = time.time()
    providers.generate_seedance_video(
        config["base_url"],
        config["api_key"],
        config["model"],
        IMAGE_PROMPT,
        args.duration_seconds * 1000,
        720,
        1280,
        config["timeout_seconds"],
        dest,
        source=config["source"],
        resolution=args.resolution,
        on_submitted=submitted,
        reference_images=[image_path],
    )
    return {
        "name": name,
        "status": "completed",
        "task_id": task_id,
        "elapsed_seconds": round(time.time() - started, 2),
        "video_path": str(dest),
        "payload": _payload_media_summary(payload),
    }


def _submit_custom_payload(
    payload: dict[str, Any],
    dest: Path,
    config: dict[str, Any],
) -> tuple[str, Path]:
    submit_url = providers._seedance_endpoint(
        config["base_url"],
        providers.SEEDANCE_SUBMIT_PATH,
    )
    response = providers._request_seedance(
        submit_url,
        config["api_key"],
        timeout=min(max(config["timeout_seconds"], 5), 120),
        payload=payload,
    )
    data = response.get("data")
    if not isinstance(data, dict):
        raise RuntimeError(f"音频参考提交失败：{providers._provider_message(response)}")
    task_id = str(data.get("task_id") or data.get("taskId") or "").strip()
    if not task_id:
        raise RuntimeError(f"音频参考提交缺少 task_id：{providers._provider_message(response)}")
    providers.resume_remote_video(
        "seedance",
        config["base_url"],
        config["api_key"],
        task_id,
        config["timeout_seconds"],
        dest,
    )
    return task_id, dest


def _run_direct_audio_job(
    audio_path: Path,
    anchor_image: Path,
    output_dir: Path,
    config: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    audio_mime = "audio/mp4" if audio_path.suffix.lower() == ".m4a" else "audio/wav"
    audio_data = base64.b64encode(audio_path.read_bytes()).decode("ascii")
    payload = providers.build_seedance_submit_payload(
        config["model"],
        AUDIO_PROMPT,
        args.duration_seconds * 1000,
        720,
        1280,
        source=f"{config['source']}-audio",
        resolution=args.resolution,
        reference_images=[anchor_image],
    )
    payload.setdefault("input_contents", []).append(
        {
            "type": "audio_url",
            "role": "reference_audio",
            "audio_url": {"url": f"data:{audio_mime};base64,{audio_data}"},
        }
    )
    dest = output_dir / "reference_audio.mp4"
    started = time.time()
    task_id, _ = _submit_custom_payload(payload, dest, config)
    return {
        "name": "audio_reference",
        "status": "completed",
        "task_id": task_id,
        "elapsed_seconds": round(time.time() - started, 2),
        "video_path": str(dest),
        "payload": _payload_media_summary(payload),
    }


def _read_image(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"无法读取图片：{path.name}")
    return image


def _read_video_frames(path: Path, timestamps: tuple[float, ...]) -> list[np.ndarray]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"无法读取视频：{path.name}")
    frames: list[np.ndarray] = []
    try:
        for timestamp in timestamps:
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            ok, frame = capture.read()
            if ok and frame is not None:
                frames.append(frame)
    finally:
        capture.release()
    if not frames:
        raise RuntimeError(f"视频没有可读取帧：{path.name}")
    return frames


def _hue_histogram(image: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    mask = (saturation >= 70) & (value >= 45)
    if int(mask.sum()) < 100:
        return np.zeros(36, dtype=np.float64)
    histogram, _ = np.histogram(
        hsv[:, :, 0][mask],
        bins=36,
        range=(0, 180),
        weights=saturation[mask].astype(np.float64),
    )
    total = float(histogram.sum())
    return histogram / total if total else histogram


def _foreground_shape_score(image: np.ndarray) -> float | None:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = ((hsv[:, :, 1] >= 55) & (hsv[:, :, 2] >= 30)).astype(np.uint8) * 255
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    if cv2.contourArea(contour) < 200:
        return None
    x, y, width, height = cv2.boundingRect(contour)
    if height <= 0:
        return None
    return round(width / height, 4)


def _cosine(left: np.ndarray, right: np.ndarray) -> float:
    length = min(left.size, right.size)
    if length <= 0:
        return 0.0
    left = left[:length]
    right = right[:length]
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denominator <= 1e-12:
        return 0.0
    return float(np.dot(left, right) / denominator)


def _analyze_images(
    images: dict[str, Path],
    jobs: dict[str, dict[str, Any]],
    output_dir: Path,
) -> dict[str, Any]:
    references = {key: _read_image(path) for key, path in images.items()}
    reference_histograms = {key: _hue_histogram(image) for key, image in references.items()}
    outputs: dict[str, list[np.ndarray]] = {}
    for key in ("a", "b"):
        job = jobs.get(f"image_{key}") or {}
        if job.get("status") != "completed":
            continue
        video_path = Path(str(job.get("video_path") or ""))
        if not video_path.is_file():
            continue
        frames = _read_video_frames(video_path, (0.3, 1.8, 3.3))
        outputs[key] = frames
        _write_contact_sheet(
            output_dir / f"image_{key}_contact.jpg",
            references[key],
            frames,
        )
    if set(outputs) != {"a", "b"}:
        return {"status": "inconclusive", "reason": "至少一个图片参考视频未成功生成"}

    output_histograms = {
        key: np.mean([_hue_histogram(frame) for frame in frames], axis=0)
        for key, frames in outputs.items()
    }
    similarities = {
        output_key: {
            reference_key: round(
                _cosine(output_histograms[output_key], reference_histograms[reference_key]),
                4,
            )
            for reference_key in ("a", "b")
        }
        for output_key in ("a", "b")
    }
    shape_aspects = {
        "reference_a": _foreground_shape_score(references["a"]),
        "reference_b": _foreground_shape_score(references["b"]),
        "output_a": [_foreground_shape_score(frame) for frame in outputs["a"]],
        "output_b": [_foreground_shape_score(frame) for frame in outputs["b"]],
    }
    margins = {
        "a": similarities["a"]["a"] - similarities["a"]["b"],
        "b": similarities["b"]["b"] - similarities["b"]["a"],
    }
    effective = (
        similarities["a"]["a"] > similarities["a"]["b"]
        and similarities["b"]["b"] > similarities["b"]["a"]
        and margins["a"] >= 0.03
        and margins["b"] >= 0.03
    )
    return {
        "status": "effective" if effective else "not_effective",
        "histogram_similarities": similarities,
        "diagonal_margin": {key: round(value, 4) for key, value in margins.items()},
        "shape_aspects": shape_aspects,
        "frames_analyzed": {key: len(frames) for key, frames in outputs.items()},
    }


def _write_contact_sheet(path: Path, reference: np.ndarray, frames: list[np.ndarray]) -> None:
    height = 480

    def fit(image: np.ndarray) -> np.ndarray:
        scale = height / max(image.shape[0], 1)
        width = max(1, int(round(image.shape[1] * scale)))
        return cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)

    panels = [fit(reference), *[fit(frame) for frame in frames]]
    cv2.imwrite(str(path), cv2.hconcat(panels))


def _extract_audio(video_path: Path, dest: Path) -> None:
    subprocess.run(
        [
            ffmpeg_bin(),
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(dest),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _read_pcm16_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        sample_rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, sample_rate


def _spectral_signature(samples: np.ndarray, sample_rate: int) -> tuple[np.ndarray, np.ndarray]:
    n_fft = 4096
    hop = n_fft // 2
    if samples.size < n_fft:
        samples = np.pad(samples, (0, n_fft - samples.size))
    window = np.hanning(n_fft)
    spectra = []
    for start in range(0, max(samples.size - n_fft + 1, 1), hop):
        frame = samples[start:start + n_fft]
        if frame.size < n_fft:
            frame = np.pad(frame, (0, n_fft - frame.size))
        spectra.append(np.abs(np.fft.rfft(frame * window)))
    magnitude = np.mean(spectra, axis=0)
    frequencies = np.fft.rfftfreq(n_fft, d=1.0 / sample_rate)
    return frequencies, magnitude


def _frequency_peak_hits(
    frequencies: np.ndarray,
    magnitude: np.ndarray,
    expected: tuple[float, ...],
    tolerance_hz: float = 25.0,
) -> list[dict[str, Any]]:
    results = []
    for target in expected:
        index = int(np.argmin(np.abs(frequencies - target)))
        low = max(0, int(np.searchsorted(frequencies, target - tolerance_hz)))
        high = min(magnitude.size, int(np.searchsorted(frequencies, target + tolerance_hz)) + 1)
        local = float(magnitude[index])
        around = np.concatenate(
            [
                magnitude[max(0, low - 20):low],
                magnitude[high:min(magnitude.size, high + 20)],
            ]
        )
        baseline = float(np.median(around)) if around.size else 0.0
        ratio = local / max(baseline, 1e-9)
        results.append(
            {
                "target_hz": target,
                "actual_hz": round(float(frequencies[index]), 2),
                "peak_ratio": round(ratio, 3),
                "hit": ratio >= 2.0,
            }
        )
    return results


def _envelope(samples: np.ndarray, sample_rate: int, window_seconds: float = 0.1) -> np.ndarray:
    window = max(1, int(sample_rate * window_seconds))
    values = []
    for start in range(0, samples.size, window):
        chunk = samples[start:start + window]
        if chunk.size == 0:
            continue
        values.append(float(np.sqrt(np.mean(np.square(chunk)))))
    return np.asarray(values, dtype=np.float64)


def _top_peaks(
    frequencies: np.ndarray,
    magnitude: np.ndarray,
    limit: int = 8,
) -> list[dict[str, float]]:
    indices = np.argsort(magnitude)[-limit:][::-1]
    return [
        {
            "frequency_hz": round(float(frequencies[index]), 2),
            "magnitude": round(float(magnitude[index]), 6),
        }
        for index in indices
        if 20 <= frequencies[index] <= 7000
    ]


def _write_audio_spectrogram(
    reference: np.ndarray,
    generated: np.ndarray,
    sample_rate: int,
    path: Path,
) -> None:
    def matrix(samples: np.ndarray) -> np.ndarray:
        n_fft = 1024
        hop = 256
        if samples.size < n_fft:
            samples = np.pad(samples, (0, n_fft - samples.size))
        frames = []
        window = np.hanning(n_fft)
        for start in range(0, max(samples.size - n_fft + 1, 1), hop):
            frame = samples[start:start + n_fft]
            if frame.size < n_fft:
                frame = np.pad(frame, (0, n_fft - frame.size))
            frames.append(np.abs(np.fft.rfft(frame * window)))
        values = 20 * np.log10(np.asarray(frames).T + 1e-6)
        frequencies = np.fft.rfftfreq(n_fft, d=1.0 / sample_rate)
        values = values[frequencies <= 5000]
        values -= float(values.min())
        maximum = max(float(values.max()), 1e-6)
        normalized = np.clip(values / maximum * 255.0, 0, 255).astype(np.uint8)
        image = cv2.applyColorMap(normalized, cv2.COLORMAP_TURBO)
        return cv2.resize(
            image,
            (900, 220),
            interpolation=cv2.INTER_LINEAR,
        )

    reference_image = matrix(reference)
    generated_image = matrix(generated)
    cv2.putText(
        reference_image,
        "reference audio",
        (12, 28),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        generated_image,
        "generated video audio",
        (12, 28),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    cv2.imwrite(str(path), cv2.vconcat([reference_image, generated_image]))


def _analyze_audio(
    reference_wav: Path,
    job: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    if job.get("status") != "completed":
        return {
            "status": "inconclusive",
            "reason": "Seedance 未成功完成直接 reference_audio 任务",
        }
    video_path = Path(str(job.get("video_path") or ""))
    output_wav = output_dir / "reference_audio_output.wav"
    try:
        _extract_audio(video_path, output_wav)
    except subprocess.CalledProcessError as exc:
        return {
            "status": "not_effective",
            "reason": "生成视频没有可提取音轨",
            "ffmpeg_error": (exc.stderr or "")[-800:],
        }
    reference, sample_rate = _read_pcm16_wav(reference_wav)
    generated, generated_rate = _read_pcm16_wav(output_wav)
    if generated_rate != sample_rate:
        ratio = sample_rate / max(generated_rate, 1)
        target_length = max(1, int(round(generated.size * ratio)))
        generated = np.interp(
            np.linspace(0, generated.size - 1, target_length, dtype=np.float64),
            np.arange(generated.size, dtype=np.float64),
            generated,
        ).astype(np.float32)
    ref_frequencies, ref_magnitude = _spectral_signature(reference, sample_rate)
    out_frequencies, out_magnitude = _spectral_signature(generated, sample_rate)
    spectral_cosine = _cosine(
        np.log1p(ref_magnitude),
        np.log1p(out_magnitude),
    )
    envelope_cosine = _cosine(
        _envelope(reference, sample_rate),
        _envelope(generated, sample_rate),
    )
    peak_hits = _frequency_peak_hits(out_frequencies, out_magnitude, REFERENCE_FREQUENCIES)
    hit_count = sum(1 for item in peak_hits if item["hit"])
    spectrogram_path = output_dir / "audio_spectrogram_compare.jpg"
    _write_audio_spectrogram(
        reference,
        generated,
        sample_rate,
        spectrogram_path,
    )
    effective = spectral_cosine >= 0.45 and envelope_cosine >= 0.35 and hit_count >= 2
    return {
        "status": "effective" if effective else "not_effective",
        "spectral_cosine": round(spectral_cosine, 4),
        "envelope_cosine": round(envelope_cosine, 4),
        "expected_peak_hits": peak_hits,
        "expected_peak_hit_count": hit_count,
        "reference_top_peaks": _top_peaks(ref_frequencies, ref_magnitude),
        "generated_top_peaks": _top_peaks(out_frequencies, out_magnitude),
        "reference_rms": round(float(np.sqrt(np.mean(np.square(reference)))), 6),
        "generated_rms": round(float(np.sqrt(np.mean(np.square(generated)))), 6),
        "generated_audio_path": str(output_wav),
        "spectrogram_path": str(spectrogram_path),
    }


def _safe_error(exc: BaseException) -> str:
    text = str(exc or "").strip() or exc.__class__.__name__
    if len(text) > 1200:
        text = text[:1200] + "..."
    return text


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir
        else Path(tempfile.gettempdir()) / f"seedance-reference-verification-{timestamp}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    config = _resolve_model_settings(args)
    print(
        json.dumps(
            {
                "output_dir": str(output_dir),
                "user_id": config["user_id"],
                "provider": "seedance",
                "model": config["model"],
                "api_key_present": True,
            },
            ensure_ascii=False,
        )
    )

    images = (
        {}
        if args.skip_images and args.skip_audio
        else _create_reference_images(output_dir)
    )
    reference_wav: Path | None = None
    reference_m4a: Path | None = None
    if not args.skip_audio:
        reference_wav, reference_m4a = _create_reference_audio(output_dir)

    project_payload_audit = {
        "image_payload": _payload_media_summary(
            providers.build_seedance_submit_payload(
                config["model"],
                IMAGE_PROMPT,
                args.duration_seconds * 1000,
                720,
                1280,
                source=config["source"],
                resolution=args.resolution,
                reference_images=[images["a"]] if images else [],
            )
        ),
        "audio_payload_from_project_code": _payload_media_summary(
            providers.build_seedance_submit_payload(
                config["model"],
                AUDIO_PROMPT,
                args.duration_seconds * 1000,
                720,
                1280,
                source=config["source"],
                resolution=args.resolution,
            )
        ),
    }

    jobs: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 3))) as pool:
        futures = {}
        if not args.skip_images:
            futures[pool.submit(
                _run_project_image_job,
                "image_a",
                images["a"],
                output_dir,
                config,
                args,
            )] = "image_a"
            futures[pool.submit(
                _run_project_image_job,
                "image_b",
                images["b"],
                output_dir,
                config,
                args,
            )] = "image_b"
        if not args.skip_audio and reference_wav is not None:
            futures[pool.submit(
                _run_direct_audio_job,
                reference_wav,
                images["a"],
                output_dir,
                config,
                args,
            )] = "audio_reference"
        for future in as_completed(futures):
            name = futures[future]
            try:
                jobs[name] = future.result()
                print(f"[completed] {name} task={jobs[name].get('task_id') or '-'}")
            except Exception as exc:  # noqa: BLE001
                jobs[name] = {
                    "name": name,
                    "status": "failed",
                    "error": _safe_error(exc),
                }
                print(f"[failed] {name}: {_safe_error(exc)}")

    image_analysis = (
        {"status": "skipped"}
        if args.skip_images
        else _analyze_images(images, jobs, output_dir)
    )
    audio_analysis = (
        {"status": "skipped"}
        if args.skip_audio or reference_wav is None
        else _analyze_audio(reference_wav, jobs.get("audio_reference") or {}, output_dir)
    )
    report = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "provider": "seedance",
        "model": config["model"],
        "user_id": config["user_id"],
        "resolution": args.resolution,
        "duration_seconds": args.duration_seconds,
        "assets": {
            "reference_image_a": str(images.get("a") or ""),
            "reference_image_b": str(images.get("b") or ""),
            "reference_audio_wav": str(reference_wav or ""),
            "reference_audio_m4a": str(reference_m4a or ""),
        },
        "project_payload_audit": project_payload_audit,
        "jobs": jobs,
        "analysis": {
            "reference_images": image_analysis,
            "reference_audio": audio_analysis,
        },
    }
    report_path = output_dir / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"REPORT={report_path}")
    failed = any(job.get("status") == "failed" for job in jobs.values())
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
