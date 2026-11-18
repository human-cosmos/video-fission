from __future__ import annotations

import base64
import json
import mimetypes
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import media


class ProviderError(RuntimeError):
    pass


def chat_completions(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    timeout: int,
) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    body = json.dumps(
        {
            "model": model or "gpt-4o-mini",
            "messages": messages,
            "temperature": 0.2,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ProviderError(f"模型请求失败 HTTP {exc.code}") from exc
    except Exception as exc:
        raise ProviderError(f"模型请求失败：{exc.__class__.__name__}") from exc
    try:
        return str(payload["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError("模型响应缺少 content") from exc


def extract_json(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re_strip_fence(raw)
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        data = json.loads(raw[start : end + 1])
        if isinstance(data, dict):
            return data
    raise ProviderError("模型未返回合法 JSON")


def re_strip_fence(raw: str) -> str:
    lines = raw.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)


def encode_image(path: Path) -> dict[str, Any]:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime};base64,{data}"},
    }


def generate_remote_video(
    provider: str,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    duration_ms: int,
    width: int,
    height: int,
    fps: float,
    timeout: int,
    dest: Path,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "model": model,
        "prompt": prompt,
        "duration_ms": duration_ms,
        "width": width,
        "height": height,
        "fps": fps,
    }
    if provider == "generic_http":
        url = base_url.rstrip("/")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
    else:
        url = base_url.rstrip("/") + "/video/generations"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            data = response.read()
    except urllib.error.HTTPError as exc:
        raise ProviderError(f"视频生成失败 HTTP {exc.code}") from exc
    except Exception as exc:
        raise ProviderError(f"视频生成失败：{exc.__class__.__name__}") from exc
    if "video/" in content_type or data[:8].find(b"ftyp") != -1 or data.startswith(b"\x00\x00\x00"):
        dest.write_bytes(data)
        return dest
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderError("视频生成响应无法识别") from exc
    video_url = parsed.get("url")
    job_id = parsed.get("job_id")
    if job_id and not video_url:
        video_url = _poll_job(base_url, api_key, job_id, timeout)
    if not video_url:
        raise ProviderError("视频生成响应缺少 url")
    _download(video_url, dest, api_key, timeout)
    return dest


def _poll_job(base_url: str, api_key: str, job_id: str, timeout: int) -> str:
    import time

    url = base_url.rstrip("/") + f"/jobs/{job_id}"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    deadline = time.time() + max(timeout, 5)
    while time.time() < deadline:
        request = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(request, timeout=20) as response:
            parsed = json.loads(response.read().decode("utf-8"))
        status = str(parsed.get("status") or "")
        if status == "completed" and parsed.get("url"):
            return str(parsed["url"])
        if status in {"failed", "error"}:
            raise ProviderError("视频生成任务失败")
        time.sleep(1.5)
    raise ProviderError("视频生成任务超时")


def _download(url: str, dest: Path, api_key: str, timeout: int) -> None:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        dest.write_bytes(response.read())


def synthesize_speech(
    base_url: str,
    api_key: str,
    model: str,
    voice: str,
    text: str,
    timeout: int,
    dest: Path,
) -> Path:
    url = base_url.rstrip("/") + "/audio/speech"
    body = json.dumps(
        {
            "model": model or "gpt-4o-mini-tts",
            "voice": voice or "alloy",
            "input": text,
            "format": "mp3",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            dest.write_bytes(response.read())
    except Exception as exc:
        raise ProviderError(f"TTS 失败：{exc.__class__.__name__}") from exc
    return dest


# Keep media import used for local fallback helpers re-export
probe = media.probe
