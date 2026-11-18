from __future__ import annotations

import base64
import json
import logging
import math
import mimetypes
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from threading import Event
from typing import Any

from . import media
from .logging_utils import prompt_preview, safe_url, summarize_messages

logger = logging.getLogger(__name__)


class ProviderError(RuntimeError):
    pass


class ProviderCancelled(ProviderError):
    pass


SEEDANCE_SUBMIT_PATH = "/acore/model/v1/task/generate/video"
SEEDANCE_TASK_PATH = "/acore/model/v1/task/video/{task_id}"
SEEDANCE_MODELS_PATH = "/acore/model/v1/models"


def chat_completions(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    timeout: int,
) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    model_name = model or "gpt-4o-mini"
    body = json.dumps(
        {
            "model": model_name,
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
    stats = summarize_messages(messages)
    started = time.perf_counter()
    logger.info(
        "llm.request_started provider=openai_compatible endpoint=%s model=%s messages=%s roles=%s "
        "prompt_chars=%s image_parts=%s video_parts=%s timeout_s=%s",
        safe_url(url),
        model_name,
        stats["messages"],
        stats["roles"],
        stats["text_chars"],
        stats["image_parts"],
        stats["video_parts"],
        timeout,
    )
    preview = prompt_preview(
        "\n".join(
            str(message.get("content") or "")
            for message in messages
            if isinstance(message.get("content"), str)
        )
    )
    if preview:
        logger.info("llm.prompt_preview provider=openai_compatible preview=%s", preview)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            payload = json.loads(raw)
    except urllib.error.HTTPError as exc:
        logger.warning(
            "llm.request_failed provider=openai_compatible status=%s elapsed_ms=%.1f",
            exc.code,
            (time.perf_counter() - started) * 1000,
        )
        raise ProviderError(f"模型请求失败 HTTP {exc.code}") from exc
    except Exception as exc:
        logger.warning(
            "llm.request_failed provider=openai_compatible error=%s elapsed_ms=%.1f",
            exc.__class__.__name__,
            (time.perf_counter() - started) * 1000,
            exc_info=True,
        )
        raise ProviderError(f"模型请求失败：{exc.__class__.__name__}") from exc
    try:
        content = str(payload["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as exc:
        logger.warning("llm.response_invalid provider=openai_compatible payload_keys=%s", list(payload)[:12])
        raise ProviderError("模型响应缺少 content") from exc
    logger.info(
        "llm.request_completed provider=openai_compatible status=%s response_chars=%s elapsed_ms=%.1f",
        getattr(response, "status", "-"),
        len(content),
        (time.perf_counter() - started) * 1000,
    )
    preview = prompt_preview(content, 1000)
    if preview:
        logger.debug("llm.response_preview provider=openai_compatible preview=%s", preview)
    return content


def extract_json(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re_strip_fence(raw)
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            logger.debug("llm.json_parsed mode=direct keys=%s", list(data)[:16])
            return data
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        data = json.loads(raw[start : end + 1])
        if isinstance(data, dict):
            logger.debug("llm.json_parsed mode=embedded keys=%s", list(data)[:16])
            return data
    preview = prompt_preview(raw, 240)
    if preview:
        logger.warning("llm.json_parse_failed chars=%s preview=%s", len(raw), preview)
    else:
        logger.warning("llm.json_parse_failed chars=%s", len(raw))
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
    source: str = "",
    resolution: str = "720p",
    cancel: Event | None = None,
    input_contents: list[dict[str, Any]] | None = None,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    logger.info(
        "video.generate_request_started provider=%s endpoint=%s model=%s duration_ms=%s "
        "target=%sx%s fps=%.3f prompt_chars=%s inputs=%s",
        provider,
        safe_url(base_url),
        model or "-",
        duration_ms,
        width,
        height,
        fps,
        len(prompt),
        len(input_contents or []),
    )
    preview = prompt_preview(prompt)
    if preview:
        logger.info("video.prompt_preview provider=%s preview=%s", provider, preview)
    if provider == "seedance":
        return generate_seedance_video(
            base_url,
            api_key,
            model,
            prompt,
            duration_ms,
            width,
            height,
            timeout,
            dest,
            source=source,
            resolution=resolution,
            cancel=cancel,
            input_contents=input_contents,
        )
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
        logger.warning(
            "video.generate_request_failed provider=%s status=%s elapsed_ms=%.1f",
            provider,
            exc.code,
            (time.perf_counter() - started) * 1000,
        )
        raise ProviderError(f"视频生成失败 HTTP {exc.code}") from exc
    except Exception as exc:
        logger.warning(
            "video.generate_request_failed provider=%s error=%s elapsed_ms=%.1f",
            provider,
            exc.__class__.__name__,
            (time.perf_counter() - started) * 1000,
            exc_info=True,
        )
        raise ProviderError(f"视频生成失败：{exc.__class__.__name__}") from exc
    logger.info(
        "video.generate_response_received provider=%s status=%s content_type=%s bytes=%s elapsed_ms=%.1f",
        provider,
        getattr(response, "status", "-"),
        content_type or "-",
        len(data),
        (time.perf_counter() - started) * 1000,
    )
    if "video/" in content_type or data[:8].find(b"ftyp") != -1 or data.startswith(b"\x00\x00\x00"):
        dest.write_bytes(data)
        logger.info("video.generate_download_completed provider=%s output=%s bytes=%s", provider, dest.name, len(data))
        return dest
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderError("视频生成响应无法识别") from exc
    video_url = parsed.get("url")
    job_id = parsed.get("job_id")
    logger.info(
        "video.generate_response_parsed provider=%s job_id=%s video_url=%s",
        provider,
        job_id or "-",
        safe_url(str(video_url or "")),
    )
    if job_id and not video_url:
        video_url = _poll_job(base_url, api_key, job_id, timeout, cancel)
    if not video_url:
        raise ProviderError("视频生成响应缺少 url")
    _download(video_url, dest, api_key, timeout, base_url)
    logger.info(
        "video.generate_request_completed provider=%s output=%s size=%s elapsed_ms=%.1f",
        provider,
        dest.name,
        dest.stat().st_size,
        (time.perf_counter() - started) * 1000,
    )
    return dest


def build_reference_input_contents(
    video_url: str = "",
    vocals_url: str = "",
    bgm_url: str = "",
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if video_url:
        items.append({"type": "video_url", "role": "reference_video", "url": video_url})
    if vocals_url:
        items.append({"type": "audio_url", "role": "reference_audio", "url": vocals_url})
    if bgm_url:
        items.append({"type": "audio_url", "role": "reference_audio", "url": bgm_url})
    return items


def public_media_url(base_url: str, production_id: str, name: str) -> tuple[str, str]:
    """Return (url, warning). Never claim a localhost URL is publicly reachable."""
    base = str(base_url or "").strip().rstrip("/")
    name = str(name or "").lstrip("/")
    path = f"/api/productions/{production_id}/artifacts/{name}"
    if not base:
        return "", "未配置 media_public_base_url，参考音视频未写成公网 URL，Seedance 无法保证能拉取"
    from urllib.parse import urlparse

    host = (urlparse(base).hostname or "").lower()
    url = f"{base}{path}"
    private = host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".localhost")
    if private or host.startswith("10.") or host.startswith("192.168.") or host.startswith("172."):
        return url, "media_public_base_url 指向本机或内网，Seedance 通常无法拉取，请改成公网可访问地址"
    return url, ""


def build_seedance_submit_payload(
    model: str,
    prompt: str,
    duration_ms: int,
    width: int,
    height: int,
    source: str = "",
    resolution: str = "720p",
    input_contents: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = {
        "model": _seedance_model(model),
        "prompt": prompt,
        "duration": max(1, int(math.ceil(max(duration_ms, 1) / 1000))),
        "mode": "async_poll",
        "source": source or "video-fission",
        "aspect_ratio": _seedance_aspect_ratio(width, height),
        "output_format": "mp4",
        "options": {
            "resolution": resolution if resolution in {"480p", "720p"} else "720p",
            "watermark": False,
            "generate_audio": True,
            "return_last_frame": False,
        },
    }
    if input_contents:
        payload["input_contents"] = list(input_contents)
    return payload


def generate_seedance_video(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    duration_ms: int,
    width: int,
    height: int,
    timeout: int,
    dest: Path,
    source: str = "",
    resolution: str = "720p",
    cancel: Event | None = None,
    input_contents: list[dict[str, Any]] | None = None,
) -> Path:
    _raise_if_cancelled(cancel)
    started = time.perf_counter()
    if not base_url:
        raise ProviderError("Seedance 未配置 base_url")
    if not api_key:
        raise ProviderError("Seedance 未配置 API Key")
    payload = build_seedance_submit_payload(
        model,
        prompt,
        duration_ms,
        width,
        height,
        source=source,
        resolution=resolution,
        input_contents=input_contents,
    )
    submit_url = _seedance_endpoint(base_url, SEEDANCE_SUBMIT_PATH)
    logger.info(
        "video.seedance_submit_started endpoint=%s model=%s duration_ms=%s "
        "target=%sx%s resolution=%s prompt_chars=%s inputs=%s",
        safe_url(submit_url),
        payload.get("model"),
        duration_ms,
        width,
        height,
        resolution,
        len(prompt),
        len(input_contents or []),
    )
    response = _request_seedance(
        submit_url,
        api_key,
        timeout=min(max(timeout, 5), 120),
        payload=payload,
    )
    data = response.get("data")
    if not isinstance(data, dict):
        raise ProviderError(f"Seedance 提交失败：{_provider_message(response)}")
    task_id = str(data.get("task_id") or data.get("taskId") or "").strip()
    if not task_id:
        raise ProviderError(f"Seedance 提交响应缺少 task_id：{_provider_message(response)}")
    logger.info("video.seedance_task_created task_id=%s", task_id)
    # The submit response currently returns a gateway-local /api/v1/tasks path
    # that is not exposed on api.giikin.com. The documented /acore route is the
    # stable task query endpoint.
    deadline = time.monotonic() + max(timeout, 10)
    last_status = ""
    poll_count = 0
    while time.monotonic() < deadline:
        _raise_if_cancelled(cancel)
        poll_count += 1
        query_url = _seedance_query_url(base_url, task_id)
        task = _request_seedance(query_url, api_key, timeout=min(30, max(timeout, 5)))
        task_data = task.get("data")
        if not isinstance(task_data, dict):
            message = _provider_message(task)
            if "不存在" in message:
                time.sleep(1)
                continue
            raise ProviderError(f"Seedance 任务查询失败：{message}")
        status = str(task_data.get("status") or "").strip().lower()
        if status != last_status:
            logger.info("video.seedance_status task_id=%s status=%s poll=%s", task_id, status or "-", poll_count)
        last_status = status
        if last_status == "completed":
            video_url = _seedance_video_url(task_data)
            if not video_url:
                raise ProviderError("Seedance 任务已完成但未返回视频 URL")
            logger.info(
                "video.seedance_completed task_id=%s poll=%s video_url=%s elapsed_ms=%.1f",
                task_id,
                poll_count,
                safe_url(video_url),
                (time.perf_counter() - started) * 1000,
            )
            _download_seedance_video(video_url, dest, api_key, timeout, base_url)
            logger.info(
                "video.seedance_download_completed task_id=%s output=%s size=%s",
                task_id,
                dest.name,
                dest.stat().st_size,
            )
            return dest
        if last_status in {"failed", "cancelled", "canceled"}:
            error = task_data.get("error")
            detail = ""
            if isinstance(error, dict):
                detail = str(error.get("message") or error.get("code") or "")
            raise ProviderError(f"Seedance 任务{last_status}：{detail or _provider_message(task)}")
        if cancel is not None:
            cancel.wait(2)
        else:
            time.sleep(2)
    logger.warning("video.seedance_timeout task_id=%s last_status=%s poll=%s", task_id, last_status or "-", poll_count)
    suffix = f"，最后状态 {last_status}" if last_status else ""
    raise ProviderError(f"Seedance 视频生成超时{suffix}")


def probe_seedance(base_url: str, api_key: str, timeout: int, model: str = "") -> dict[str, Any]:
    if not base_url:
        return {"ok": False, "provider": "seedance", "detail": "未配置 base_url"}
    if not api_key:
        return {"ok": False, "provider": "seedance", "detail": "未配置 API Key"}
    url = _seedance_endpoint(base_url, SEEDANCE_MODELS_PATH)
    started = time.perf_counter()
    logger.info("seedance.probe_started endpoint=%s model=%s", safe_url(url), model or "-")
    request = urllib.request.Request(url, headers=_seedance_headers(api_key), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=min(max(timeout, 5), 20)) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        models = payload.get("data")
        if not isinstance(models, list):
            return {"ok": False, "provider": "seedance", "detail": "模型列表响应格式无效"}
        model_ids = {
            str(item.get("id") or "")
            for item in models
            if isinstance(item, dict) and item.get("id")
        }
        expected = _seedance_model(model)
        if expected not in model_ids:
            logger.warning("seedance.probe_failed model_not_enabled model=%s", expected)
            return {
                "ok": False,
                "provider": "seedance",
                "detail": f"模型未启用：{expected}",
            }
        result = {
            "ok": True,
            "provider": "seedance",
            "detail": f"模型已启用：{expected}",
        }
        logger.info(
            "seedance.probe_completed model=%s models=%s elapsed_ms=%.1f",
            expected,
            len(model_ids),
            (time.perf_counter() - started) * 1000,
        )
        return result
    except urllib.error.HTTPError as exc:
        logger.warning(
            "seedance.probe_failed status=%s elapsed_ms=%.1f",
            exc.code,
            (time.perf_counter() - started) * 1000,
        )
        if exc.code in {401, 403}:
            return {"ok": False, "provider": "seedance", "detail": f"认证失败 HTTP {exc.code}"}
        return {"ok": False, "provider": "seedance", "detail": f"探活失败 HTTP {exc.code}"}
    except Exception as exc:
        logger.warning("seedance.probe_failed error=%s", exc.__class__.__name__, exc_info=True)
        return {"ok": False, "provider": "seedance", "detail": f"探活失败：{exc.__class__.__name__}"}


def _request_seedance(
    url: str,
    api_key: str,
    *,
    timeout: int,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    method = "POST" if body is not None else "GET"
    started = time.perf_counter()
    request = urllib.request.Request(
        url,
        data=body,
        headers=_seedance_headers(api_key),
        method=method,
    )
    logger.info(
        "seedance.http_request_started method=%s endpoint=%s timeout_s=%s payload_keys=%s",
        method,
        safe_url(url),
        timeout,
        list(payload)[:16] if payload else "-",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        logger.warning(
            "seedance.http_request_failed method=%s endpoint=%s status=%s elapsed_ms=%.1f",
            method,
            safe_url(url),
            exc.code,
            (time.perf_counter() - started) * 1000,
        )
        if exc.code in {401, 403}:
            raise ProviderError(f"Seedance 认证失败 HTTP {exc.code}") from exc
        raise ProviderError(f"Seedance 请求失败 HTTP {exc.code}") from exc
    except Exception as exc:
        logger.warning(
            "seedance.http_request_failed method=%s endpoint=%s error=%s elapsed_ms=%.1f",
            method,
            safe_url(url),
            exc.__class__.__name__,
            (time.perf_counter() - started) * 1000,
            exc_info=True,
        )
        raise ProviderError(f"Seedance 请求失败：{exc.__class__.__name__}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProviderError("Seedance 返回了非 JSON 响应") from exc
    if not isinstance(parsed, dict):
        raise ProviderError("Seedance 返回结构不是对象")
    logger.info(
        "seedance.http_request_completed method=%s endpoint=%s status=%s response_chars=%s elapsed_ms=%.1f",
        method,
        safe_url(url),
        getattr(response, "status", "-"),
        len(raw),
        (time.perf_counter() - started) * 1000,
    )
    return parsed


def _seedance_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _seedance_endpoint(base_url: str, path: str) -> str:
    parsed = urllib.parse.urlsplit(base_url)
    base_path = parsed.path.rstrip("/")
    if base_path.endswith(path):
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, base_path, parsed.query, ""))
    marker = "/acore/model/v1"
    marker_index = base_path.find(marker)
    if marker_index >= 0:
        base_path = base_path[:marker_index]
    elif base_path.endswith("/acore"):
        base_path = base_path[:-len("/acore")]
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, base_path + path, "", ""))


def _seedance_query_url(base_url: str, task_id: str) -> str:
    return _seedance_endpoint(base_url, SEEDANCE_TASK_PATH.format(task_id=task_id))


def _seedance_aspect_ratio(width: int, height: int) -> str:
    width = max(int(width), 1)
    height = max(int(height), 1)
    ratio = width / height
    if ratio < 0.9:
        return "9:16"
    if ratio > 2.1:
        return "21:9"
    if 0.98 <= ratio <= 1.02:
        return "adaptive"
    return "16:9"


def _seedance_model(model: str) -> str:
    value = str(model or "").strip().lower()
    if value in {"", "seedance-2.0-flash", "seedance-2-0-fast", "seedance-2.0-fast", "flash"}:
        return "seedance-2-0-fast"
    if value in {"seedance-2.0", "seedance-2-0", "seedance-2.0-pro", "pro"}:
        return "seedance-2-0"
    return str(model).strip()


def _provider_message(payload: dict[str, Any]) -> str:
    text = str(payload.get("msg") or payload.get("message") or "").strip()
    if text:
        return text[:240]
    data = payload.get("data")
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("code") or "")[:240]
    code = payload.get("code")
    biz_code = payload.get("bizCode")
    return f"code={code}, bizCode={biz_code}" if code is not None or biz_code is not None else "未知错误"


def _seedance_video_url(task_data: dict[str, Any]) -> str:
    result = task_data.get("result")
    if isinstance(result, dict):
        video = result.get("video")
        if isinstance(video, dict):
            url = str(video.get("url") or "").strip()
            if url:
                return url
    response_body = task_data.get("responseBody")
    if isinstance(response_body, str) and response_body.strip():
        try:
            response_body = json.loads(response_body)
        except json.JSONDecodeError:
            response_body = None
    found = _find_video_url(response_body)
    if found:
        return found
    return _find_video_url(task_data)


def _find_video_url(value: Any) -> str:
    if isinstance(value, str):
        candidate = value.strip()
        if candidate.startswith(("http://", "https://", "data:video/")):
            return candidate
        if candidate.startswith("{") or candidate.startswith("["):
            try:
                return _find_video_url(json.loads(candidate))
            except json.JSONDecodeError:
                return ""
        return ""
    if isinstance(value, list):
        for item in value:
            found = _find_video_url(item)
            if found:
                return found
        return ""
    if not isinstance(value, dict):
        return ""
    for key in ("video", "video_url", "videoUrl", "url", "result", "data", "output", "responseBody"):
        if key not in value:
            continue
        found = _find_video_url(value[key])
        if found:
            return found
    return ""


def _download_seedance_video(
    url: str,
    dest: Path,
    api_key: str,
    timeout: int,
    api_base_url: str,
) -> None:
    started = time.perf_counter()
    logger.info("seedance.download_started url=%s", safe_url(url))
    request = urllib.request.Request(
        url,
        headers=_media_download_headers(api_base_url, url, api_key),
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get("Content-Type", "")
        data = response.read()
    if "json" in content_type or "html" in content_type:
        raise ProviderError("Seedance 视频下载地址返回了非视频内容")
    if not data:
        raise ProviderError("Seedance 视频下载结果为空")
    dest.write_bytes(data)
    logger.info(
        "seedance.download_completed url=%s content_type=%s bytes=%s elapsed_ms=%.1f",
        safe_url(url),
        content_type or "-",
        len(data),
        (time.perf_counter() - started) * 1000,
    )


def _poll_job(
    base_url: str,
    api_key: str,
    job_id: str,
    timeout: int,
    cancel: Event | None = None,
) -> str:
    url = base_url.rstrip("/") + f"/jobs/{job_id}"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    deadline = time.time() + max(timeout, 5)
    last_status = ""
    poll_count = 0
    while time.time() < deadline:
        _raise_if_cancelled(cancel)
        poll_count += 1
        request = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(request, timeout=20) as response:
            parsed = json.loads(response.read().decode("utf-8"))
        status = str(parsed.get("status") or "")
        if status != last_status:
            logger.info("video.job_status job_id=%s status=%s poll=%s", job_id, status or "-", poll_count)
        last_status = status
        if status == "completed" and parsed.get("url"):
            return str(parsed["url"])
        if status in {"failed", "error"}:
            raise ProviderError("视频生成任务失败")
        if cancel is not None:
            cancel.wait(1.5)
        else:
            time.sleep(1.5)
    raise ProviderError("视频生成任务超时")


def _download(url: str, dest: Path, api_key: str, timeout: int, api_base_url: str) -> None:
    started = time.perf_counter()
    logger.info("video.download_started url=%s", safe_url(url))
    headers = _media_download_headers(api_base_url, url, api_key)
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        dest.write_bytes(response.read())
    logger.info(
        "video.download_completed url=%s output=%s bytes=%s elapsed_ms=%.1f",
        safe_url(url),
        dest.name,
        dest.stat().st_size,
        (time.perf_counter() - started) * 1000,
    )


def _media_download_headers(api_base_url: str, media_url: str, api_key: str) -> dict[str, str]:
    if not api_key:
        return {}
    api_host = urllib.parse.urlsplit(api_base_url).netloc.lower()
    media_host = urllib.parse.urlsplit(media_url).netloc.lower()
    if api_host and api_host == media_host:
        return {"Authorization": f"Bearer {api_key}"}
    return {}


def _raise_if_cancelled(cancel: Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise ProviderCancelled("视频生成已取消")


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
    started = time.perf_counter()
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
    logger.info(
        "tts.request_started endpoint=%s model=%s voice=%s text_chars=%s timeout_s=%s",
        safe_url(url),
        model or "gpt-4o-mini-tts",
        voice or "alloy",
        len(text),
        timeout,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            dest.write_bytes(response.read())
    except Exception as exc:
        logger.warning(
            "tts.request_failed endpoint=%s error=%s elapsed_ms=%.1f",
            safe_url(url),
            exc.__class__.__name__,
            (time.perf_counter() - started) * 1000,
            exc_info=True,
        )
        raise ProviderError(f"TTS 失败：{exc.__class__.__name__}") from exc
    logger.info(
        "tts.request_completed endpoint=%s output=%s bytes=%s elapsed_ms=%.1f",
        safe_url(url),
        dest.name,
        dest.stat().st_size,
        (time.perf_counter() - started) * 1000,
    )
    return dest


# Keep media import used for local fallback helpers re-export
probe = media.probe
