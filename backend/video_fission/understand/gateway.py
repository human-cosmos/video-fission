"""可选 gemini_gateway 客户端。默认不填写任何内网地址。"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import httpx

from video_fission.logging_utils import file_summary, prompt_preview, safe_url

from .normalize import extract_json

logger = logging.getLogger(__name__)
_TOKEN_LOCK = threading.Lock()
_TOKEN_CACHE: dict[tuple[str, str], tuple[str, float]] = {}


def video_mime(path: Path | str) -> str:
    mime = mimetypes.guess_type(str(path).split("?", 1)[0])[0] or "video/mp4"
    return mime if mime.startswith("video/") else "video/mp4"


class GatewayError(RuntimeError):
    pass


class GeminiGatewayClient:
    def __init__(
        self,
        base_url: str,
        upload_url: str = "",
        token_url: str = "",
        api_key: str = "",
        model: str = "",
        source: str = "",
        max_tokens: int = 12000,
        max_retries: int = 2,
        timeout_seconds: int = 120,
    ) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.upload_url = str(upload_url or "").strip()
        self.token_url = str(token_url or "").strip()
        self.api_key = str(api_key or "")
        self.model = str(model or "")
        self.source = str(source or "")
        self.max_tokens = max(int(max_tokens or 12000), 256)
        self.max_retries = max(int(max_retries), 0)
        self.timeout_seconds = max(int(timeout_seconds or 120), 5)

    def get_access_token(self, force: bool = False) -> str:
        if not self.token_url:
            return self.api_key
        now = time.time()
        cache_key = (self.token_url, self.api_key)
        with _TOKEN_LOCK:
            cached = _TOKEN_CACHE.get(cache_key)
            if not force and cached and cached[1] > now:
                logger.info("gemini_gateway.token_cache_hit endpoint=%s", safe_url(self.token_url))
                return cached[0]
        started = time.perf_counter()
        logger.info(
            "gemini_gateway.token_request_started endpoint=%s force=%s",
            safe_url(self.token_url),
            force,
        )
        try:
            request = urllib.request.Request(self.token_url, method="GET")
            if self.api_key:
                request.add_header("Authorization", self.api_key)
            with urllib.request.urlopen(request, timeout=min(self.timeout_seconds, 30)) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace") or "{}")
            token = ""
            if isinstance(payload, dict):
                data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
                token = str(
                    (data or {}).get("client_token")
                    or (data or {}).get("access_token")
                    or (data or {}).get("token")
                    or payload.get("client_token")
                    or ""
                )
            if token:
                with _TOKEN_LOCK:
                    _TOKEN_CACHE[cache_key] = (token, now + 3600 - 300)
                logger.info(
                    "gemini_gateway.token_request_completed endpoint=%s token_chars=%s elapsed_ms=%.1f",
                    safe_url(self.token_url),
                    len(token),
                    (time.perf_counter() - started) * 1000,
                )
                return token
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "gemini_gateway.token_request_failed endpoint=%s error=%s elapsed_ms=%.1f",
                safe_url(self.token_url),
                exc.__class__.__name__,
                (time.perf_counter() - started) * 1000,
                exc_info=True,
            )
        if self.api_key:
            return self.api_key
        raise GatewayError("Gemini 网关换票失败")

    def probe(self) -> dict[str, Any]:
        if not self.base_url:
            return {"ok": False, "provider": "gemini_gateway", "detail": "未配置 base_url"}
        try:
            token = self.get_access_token()
        except GatewayError as exc:
            return {"ok": False, "provider": "gemini_gateway", "detail": str(exc)}
        if not token:
            return {"ok": False, "provider": "gemini_gateway", "detail": "未配置 API Key 或 token URL"}
        try:
            result = self.call(
                prompt='仅返回 JSON：{"ok": true}',
                max_tokens=min(self.max_tokens, 64),
                temperature=0,
            )
            if self.extract_payload(result) is not None:
                return {"ok": True, "provider": "gemini_gateway", "detail": f"已连通 {self.base_url}"}
            return {"ok": False, "provider": "gemini_gateway", "detail": "探活未返回可用结果"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "provider": "gemini_gateway", "detail": f"探活失败：{exc.__class__.__name__}"}

    def call(
        self,
        prompt: str,
        video_path: Path | None = None,
        file_uri: str = "",
        frames: list[Path] | None = None,
        temperature: float = 0.6,
        max_tokens: int | None = None,
    ) -> dict[str, Any] | None:
        if not self.base_url:
            raise GatewayError("未配置 gemini_gateway base_url")
        started = time.perf_counter()
        token = self.get_access_token()
        parts: list[dict[str, Any]] = []
        if file_uri:
            parts.append({"fileData": {"mimeType": video_mime(file_uri), "fileUri": file_uri}})
        elif video_path is not None and video_path.is_file():
            uploaded_uri = self.upload_video(video_path, token)
            if uploaded_uri:
                parts.append({"fileData": {"mimeType": video_mime(video_path), "fileUri": uploaded_uri}})
            else:
                data = base64.b64encode(video_path.read_bytes()).decode("ascii")
                parts.append({"inlineData": {"mimeType": video_mime(video_path), "data": data}})
        elif frames:
            for frame in frames:
                if not frame.is_file():
                    continue
                data = base64.b64encode(frame.read_bytes()).decode("ascii")
                mime = "image/jpeg" if frame.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
                parts.append({"inlineData": {"mimeType": mime, "data": data}})
        parts.append({"text": prompt})
        payload_body = {
            "messages": [{"role": "user", "parts": parts}],
            "model_engine": self.model,
            "max_tokens": int(max_tokens or self.max_tokens),
            "temperature": temperature,
        }
        if self.source:
            payload_body["source"] = self.source
        input_mode = "file_uri" if file_uri else ("video" if video_path else ("frames" if frames else "text"))
        logger.info(
            "gemini_gateway.request_started endpoint=%s model=%s input_mode=%s "
            "prompt_chars=%s frames=%s timeout_s=%s max_tokens=%s temperature=%.2f",
            safe_url(self.base_url),
            self.model or "-",
            input_mode,
            len(prompt),
            len(frames or []),
            self.timeout_seconds,
            int(max_tokens or self.max_tokens),
            temperature,
        )
        preview = prompt_preview(prompt)
        if preview:
            logger.info("gemini_gateway.prompt_preview preview=%s", preview)
        body = json.dumps(payload_body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = token
        raw = ""
        last_error: Exception | None = None
        refreshed_401 = False
        deadline = time.monotonic() + self.timeout_seconds
        for attempt in range(self.max_retries + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            request = urllib.request.Request(self.base_url, data=body, headers=headers, method="POST")
            logger.info(
                "gemini_gateway.request_attempt endpoint=%s attempt=%s max_attempts=%s body_bytes=%s",
                safe_url(self.base_url),
                attempt + 1,
                self.max_retries + 1,
                len(body),
            )
            try:
                with urllib.request.urlopen(request, timeout=max(0.5, remaining)) as response:
                    raw = response.read().decode("utf-8", errors="replace")
                    logger.info(
                        "gemini_gateway.response_received status=%s response_chars=%s elapsed_ms=%.1f",
                        getattr(response, "status", "-"),
                        len(raw),
                        (time.perf_counter() - started) * 1000,
                    )
                break
            except urllib.error.HTTPError as exc:
                logger.warning(
                    "gemini_gateway.request_attempt_failed attempt=%s status=%s elapsed_ms=%.1f",
                    attempt + 1,
                    exc.code,
                    (time.perf_counter() - started) * 1000,
                )
                if exc.code == 401 and self.token_url and not refreshed_401:
                    refreshed_401 = True
                    logger.warning("gemini_gateway.unauthorized_refresh attempt=%s", attempt + 1)
                    token = self.get_access_token(force=True)
                    if token:
                        headers["Authorization"] = token
                        continue
                last_error = GatewayError(f"HTTP {exc.code}")
                if 400 <= exc.code < 500 and exc.code != 429:
                    logger.warning("gemini_gateway.request_failed status=%s retry=false", exc.code)
                    raise last_error from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = GatewayError(f"{exc.__class__.__name__}")
                logger.warning(
                    "gemini_gateway.request_attempt_failed attempt=%s error=%s",
                    attempt + 1,
                    exc.__class__.__name__,
                )
            if attempt < self.max_retries:
                delay = min(2**attempt, max(0.0, deadline - time.monotonic()))
                if delay > 0:
                    logger.info("gemini_gateway.retry_scheduled attempt=%s delay_s=%.2f", attempt + 1, delay)
                    time.sleep(delay)
        if not raw:
            raise last_error or GatewayError("Gemini 请求超时")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"data": raw}
        if isinstance(payload, dict) and payload.get("code") not in (None, 0, "0"):
            raise GatewayError(f"gateway code={payload.get('code')}")
        logger.info(
            "gemini_gateway.request_completed input_mode=%s response_chars=%s elapsed_ms=%.1f",
            input_mode,
            len(raw),
            (time.perf_counter() - started) * 1000,
        )
        return payload if isinstance(payload, dict) else {"data": payload}

    def extract_payload(self, response: dict[str, Any] | None) -> dict[str, Any] | list[Any] | None:
        if not response:
            return None
        for key in ("data", "result", "text", "content", "message", "output", "answer"):
            if key in response:
                payload = _extract_payload_value(response.get(key))
                if payload is not None:
                    return payload
        if any(key in response for key in ("video_lens", "video_summary", "shot_observation_card", "ok")):
            return response
        return None

    def upload_video(self, path: Path, token: str) -> str:
        if not self.upload_url:
            return ""
        mime = video_mime(path)
        started = time.perf_counter()
        logger.info(
            "gemini_gateway.upload_started endpoint=%s file=%s mime=%s",
            safe_url(self.upload_url),
            file_summary(path),
            mime,
        )
        try:
            with path.open("rb") as handle:
                response = httpx.post(
                    self.upload_url,
                    files={"file": (path.name, handle, mime)},
                    headers={"Authorization": token} if token else {},
                    timeout=min(self.timeout_seconds, 120),
                    follow_redirects=True,
                )
            response.raise_for_status()
            payload = response.json()
        except (OSError, httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "gemini_gateway.upload_failed endpoint=%s error=%s fallback=inline",
                safe_url(self.upload_url),
                exc.__class__.__name__,
                exc_info=True,
            )
            return ""
        if not isinstance(payload, dict) or payload.get("code") not in (None, 0, 200, "0", "200"):
            return ""
        data = payload.get("data")
        if not isinstance(data, dict):
            return ""
        uri = str(data.get("gsutil_uri") or data.get("url") or data.get("file_uri") or "")
        logger.info(
            "gemini_gateway.upload_completed endpoint=%s status=%s uri=%s elapsed_ms=%.1f",
            safe_url(self.upload_url),
            getattr(response, "status_code", "-"),
            safe_url(uri),
            (time.perf_counter() - started) * 1000,
        )
        return uri


def _extract_payload_value(value: Any, depth: int = 0) -> dict[str, Any] | list[Any] | None:
    if depth > 6 or value is None:
        return None
    if isinstance(value, str):
        parsed = extract_json(value)
        if isinstance(parsed, (dict, list)) and parsed:
            return parsed
        return None
    if isinstance(value, list):
        return value if value else None
    if not isinstance(value, dict):
        return None
    for key in ("data", "result", "text", "content", "message", "output", "answer"):
        if key not in value:
            continue
        parsed = _extract_payload_value(value.get(key), depth + 1)
        if parsed is not None:
            return parsed
    return value if value else None
