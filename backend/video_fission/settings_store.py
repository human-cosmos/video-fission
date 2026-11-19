from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from copy import deepcopy
from typing import Any

from . import media
from .config import Settings
from .db import Database
from .logging_utils import safe_url

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS: dict[str, Any] = {
    "multimodal": {
        "provider": "local_ffmpeg",
        "base_url": "",
        "upload_url": "",
        "token_url": "",
        "source": "",
        "api_key_set": False,
        "model": "",
        "max_tokens": 12000,
        "timeout_seconds": 120,
    },
    "video_generator": {
        "provider": "seedance",
        "base_url": "https://api.giikin.com",
        "source": "video-fission",
        "resolution": "720p",
        "api_key_set": False,
        "model": "seedance-2-0-fast",
        "timeout_seconds": 600,
        "media_public_base_url": "",
    },
    "understand": {
        "transition_detection": True,
        "clip_analysis": True,
        "observe": True,
        "clip_workers": 3,
        "gemini_max_size_mb": 15,
        "observe_timeout_seconds": 360,
    },
}

PROVIDER_ENUMS = {
    "multimodal": {"local_ffmpeg", "openai_compatible", "gemini_gateway"},
    "video_generator": {"openai_compatible", "generic_http", "seedance"},
}

SECRET_KEYS = ("multimodal", "video_generator")


class SettingsStore:
    def __init__(self, db: Database, runtime_settings: Settings | None = None):
        self.db = db
        self.runtime_settings = runtime_settings

    def public_settings(self, user_id: str | None = None) -> dict[str, Any]:
        public, secrets = self._load(user_id)
        return self._mask(public, secrets)

    def secret_for(self, target: str, user_id: str | None = None) -> str:
        _, secrets = self._load(user_id)
        return str(secrets.get(target) or "")

    def raw(self, user_id: str | None = None) -> tuple[dict[str, Any], dict[str, str]]:
        return self._load(user_id)

    def update(
        self,
        patch: dict[str, Any],
        user_id: str | None = None,
    ) -> dict[str, Any]:
        public, secrets = self._load(user_id)
        for section in ("multimodal", "video_generator", "understand"):
            if section not in patch or patch[section] is None:
                continue
            if not isinstance(patch[section], dict):
                raise ValueError(f"{section} 必须是对象")
            incoming = patch[section]
            if section in PROVIDER_ENUMS and "provider" in incoming and incoming["provider"] is not None:
                provider = str(incoming["provider"]).strip()
                if provider not in PROVIDER_ENUMS[section]:
                    raise ValueError(f"{section}.provider 不支持 {provider}")
                public[section]["provider"] = provider
            for field in (
                "base_url",
                "upload_url",
                "token_url",
                "source",
                "model",
                "voice",
                "resolution",
                "media_public_base_url",
            ):
                if field in incoming and incoming[field] is not None and field in public[section]:
                    public[section][field] = str(incoming[field]).strip()
            if section == "video_generator":
                resolution = str(public[section].get("resolution") or "").strip()
                if resolution not in {"480p", "720p"}:
                    raise ValueError("video_generator.resolution 只能是 480p 或 720p")
            if section == "multimodal" and "max_tokens" in incoming and incoming["max_tokens"] is not None:
                max_tokens = int(incoming["max_tokens"])
                if max_tokens < 256 or max_tokens > 65536:
                    raise ValueError("multimodal.max_tokens 需在 256 到 65536 之间")
                public[section]["max_tokens"] = max_tokens
            if section != "understand" and "timeout_seconds" in incoming and incoming["timeout_seconds"] is not None:
                timeout = int(incoming["timeout_seconds"])
                if timeout < 5 or timeout > 3600:
                    raise ValueError("timeout_seconds 需在 5 到 3600 之间")
                if "timeout_seconds" in public[section]:
                    public[section]["timeout_seconds"] = timeout
            if section == "understand":
                for flag in ("transition_detection", "clip_analysis", "observe"):
                    if flag in incoming and incoming[flag] is not None:
                        public[section][flag] = bool(incoming[flag])
                if "clip_workers" in incoming and incoming["clip_workers"] is not None:
                    workers = int(incoming["clip_workers"])
                    if workers < 1 or workers > 8:
                        raise ValueError("clip_workers 需在 1 到 8 之间")
                    public[section]["clip_workers"] = workers
                if "gemini_max_size_mb" in incoming and incoming["gemini_max_size_mb"] is not None:
                    size = float(incoming["gemini_max_size_mb"])
                    if size < 1 or size > 100:
                        raise ValueError("gemini_max_size_mb 需在 1 到 100 之间")
                    public[section]["gemini_max_size_mb"] = size
                if "observe_timeout_seconds" in incoming and incoming["observe_timeout_seconds"] is not None:
                    timeout = int(incoming["observe_timeout_seconds"])
                    if timeout < 30 or timeout > 3600:
                        raise ValueError("observe_timeout_seconds 需在 30 到 3600 之间")
                    public[section]["observe_timeout_seconds"] = timeout
            if section in SECRET_KEYS and "api_key" in incoming:
                # Missing key is ignored by caller; explicit null clears; "" keeps.
                value = incoming["api_key"]
                if value is None:
                    secrets[section] = ""
                elif str(value) != "":
                    secrets[section] = str(value)
        if user_id:
            self.db.upsert_user_settings(user_id, public, secrets)
        else:
            self.db.upsert_app_settings(public, secrets)
        return self._mask(public, secrets)

    def test_target(self, target: str, user_id: str | None = None) -> dict[str, Any]:
        if target not in SECRET_KEYS:
            raise ValueError("target 必须是 multimodal 或 video_generator")
        public, secrets = self._load(user_id)
        section = public[target]
        provider = section.get("provider") or "local_ffmpeg"
        started = time.perf_counter()
        logger.info(
            "settings.probe_started target=%s provider=%s endpoint=%s model=%s",
            target,
            provider,
            safe_url(str(section.get("base_url") or "")),
            section.get("model") or "-",
        )
        if provider == "local_ffmpeg":
            probe = media.ffmpeg_available()
            result = {
                "ok": bool(probe.get("ok")),
                "provider": provider,
                "detail": probe.get("version") or probe.get("detail") or "ffmpeg 不可用",
            }
            logger.info(
                "settings.probe_completed target=%s provider=%s ok=%s elapsed_ms=%.1f",
                target,
                provider,
                result["ok"],
                (time.perf_counter() - started) * 1000,
            )
            return result
        key = secrets.get(target) or ""
        base_url = str(section.get("base_url") or "").rstrip("/")
        if not base_url:
            return {"ok": False, "provider": provider, "detail": "未配置 base_url"}
        if provider == "gemini_gateway":
            from .understand.gateway import GeminiGatewayClient

            result = GeminiGatewayClient(
                base_url=base_url,
                upload_url=str(section.get("upload_url") or ""),
                token_url=str(section.get("token_url") or ""),
                api_key=key,
                model=str(section.get("model") or ""),
                source=str(section.get("source") or ""),
                max_tokens=int(section.get("max_tokens") or 12000),
                timeout_seconds=int(section.get("timeout_seconds") or 30),
            ).probe()
            logger.info(
                "settings.probe_completed target=%s provider=%s ok=%s elapsed_ms=%.1f",
                target,
                provider,
                result.get("ok"),
                (time.perf_counter() - started) * 1000,
            )
            return result
        if provider == "seedance":
            from .providers import probe_seedance

            result = probe_seedance(
                base_url,
                key,
                int(section.get("timeout_seconds") or 30),
                str(section.get("model") or ""),
            )
            logger.info(
                "settings.probe_completed target=%s provider=%s ok=%s elapsed_ms=%.1f",
                target,
                provider,
                result.get("ok"),
                (time.perf_counter() - started) * 1000,
            )
            return result
        if not key:
            return {"ok": False, "provider": provider, "detail": "未配置 API Key"}
        result = self._probe_remote(
            base_url,
            key,
            str(section.get("model") or ""),
            int(section.get("timeout_seconds") or 30),
            provider,
        )
        logger.info(
            "settings.probe_completed target=%s provider=%s ok=%s elapsed_ms=%.1f",
            target,
            provider,
            result.get("ok"),
            (time.perf_counter() - started) * 1000,
        )
        return result

    def _probe_remote(self, base_url: str, api_key: str, model: str, timeout: int, provider: str) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        models_url = f"{base_url}/models"
        started = time.perf_counter()
        logger.info("provider.probe_request_started provider=%s endpoint=%s", provider, safe_url(models_url))
        try:
            request = urllib.request.Request(models_url, headers=headers, method="GET")
            with urllib.request.urlopen(request, timeout=min(timeout, 20)) as response:
                payload = response.read(4000).decode("utf-8", errors="replace")
            if "sk-" in payload or api_key in payload:
                payload = "模型列表已返回"
            logger.info(
                "provider.probe_request_completed provider=%s endpoint=%s status=%s elapsed_ms=%.1f",
                provider,
                safe_url(models_url),
                getattr(response, "status", "-"),
                (time.perf_counter() - started) * 1000,
            )
            return {"ok": True, "provider": provider, "detail": f"已连通 {models_url}"}
        except Exception:
            logger.warning(
                "provider.probe_models_failed provider=%s endpoint=%s fallback=chat",
                provider,
                safe_url(models_url),
            )
            body = json.dumps(
                {
                    "model": model or "gpt-4o-mini",
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                }
            ).encode("utf-8")
            url = f"{base_url}/chat/completions"
            try:
                request = urllib.request.Request(url, data=body, headers=headers, method="POST")
                logger.info("provider.probe_request_started provider=%s endpoint=%s", provider, safe_url(url))
                with urllib.request.urlopen(request, timeout=min(timeout, 20)) as response:
                    response.read(2000)
                    logger.info(
                        "provider.probe_request_completed provider=%s endpoint=%s status=%s elapsed_ms=%.1f",
                        provider,
                        safe_url(url),
                        getattr(response, "status", "-"),
                        (time.perf_counter() - started) * 1000,
                    )
                return {"ok": True, "provider": provider, "detail": f"已连通 {url}"}
            except urllib.error.HTTPError as exc:
                logger.warning("provider.probe_request_failed provider=%s status=%s", provider, exc.code)
                return {"ok": False, "provider": provider, "detail": f"探活失败 HTTP {exc.code}"}
            except Exception as exc:
                logger.warning(
                    "provider.probe_request_failed provider=%s error=%s",
                    provider,
                    exc.__class__.__name__,
                    exc_info=True,
                )
                return {"ok": False, "provider": provider, "detail": f"探活失败：{exc.__class__.__name__}"}

    def _load(self, user_id: str | None = None) -> tuple[dict[str, Any], dict[str, str]]:
        row = self.db.get_user_settings(user_id) if user_id else self.db.get_app_settings()
        defaults = self._defaults()
        public = deepcopy(defaults)
        secrets = {key: "" for key in SECRET_KEYS}
        if not row:
            if user_id:
                self.db.upsert_user_settings(user_id, public, secrets)
            else:
                self.db.upsert_app_settings(public, secrets)
            return public, secrets
        stored = row.get("public") or {}
        migrated = False
        for section, section_defaults in defaults.items():
            if isinstance(stored.get(section), dict):
                public[section] = {
                    **section_defaults,
                    **{
                        key: value
                        for key, value in stored[section].items()
                        if key in section_defaults and key != "api_key"
                    },
                }
        stored_multimodal = stored.get("multimodal")
        if (
            defaults["multimodal"]["provider"] == "gemini_gateway"
            and isinstance(stored_multimodal, dict)
            and stored_multimodal.get("provider") == "local_ffmpeg"
            and not stored_multimodal.get("base_url")
            and not stored_multimodal.get("upload_url")
            and not stored_multimodal.get("token_url")
            and not stored_multimodal.get("source")
            and not stored_multimodal.get("model")
            and int(stored_multimodal.get("timeout_seconds") or 120) == 120
        ):
            public["multimodal"] = deepcopy(defaults["multimodal"])
            migrated = True
        elif (
            defaults["multimodal"]["provider"] == "gemini_gateway"
            and isinstance(stored_multimodal, dict)
            and stored_multimodal.get("provider") == "gemini_gateway"
            and stored_multimodal.get("base_url") == defaults["multimodal"].get("base_url")
            and stored_multimodal.get("upload_url") == defaults["multimodal"].get("upload_url")
            and stored_multimodal.get("token_url") == defaults["multimodal"].get("token_url")
            and stored_multimodal.get("model") == defaults["multimodal"].get("model")
            and stored_multimodal.get("source") == defaults["multimodal"].get("source")
            and int(stored_multimodal.get("timeout_seconds") or 120) == 360
        ):
            public["multimodal"]["timeout_seconds"] = 120
            migrated = True
        stored_video = stored.get("video_generator")
        if isinstance(stored_video, dict) and stored_video.get("provider") == "local_ffmpeg":
            public["video_generator"] = deepcopy(defaults["video_generator"])
            migrated = True
        for key, value in (row.get("secrets") or {}).items():
            if key in secrets:
                secrets[key] = str(value or "")
        if migrated:
            if user_id:
                self.db.upsert_user_settings(user_id, public, secrets)
            else:
                self.db.upsert_app_settings(public, secrets)
        return public, secrets

    def _defaults(self) -> dict[str, Any]:
        defaults = deepcopy(DEFAULT_SETTINGS)
        runtime = self.runtime_settings
        if runtime and runtime.gemini_api_url:
            defaults["multimodal"].update(
                {
                    "provider": "gemini_gateway",
                    "base_url": runtime.gemini_api_url,
                    "upload_url": runtime.gemini_upload_url,
                    "token_url": runtime.gemini_token_url,
                    "model": runtime.gemini_model or "gemini-3.8-flash",
                    "source": runtime.gemini_source,
                    "timeout_seconds": 120,
                }
            )
        return defaults

    def _mask(self, public: dict[str, Any], secrets: dict[str, str]) -> dict[str, Any]:
        masked = deepcopy(public)
        for key in SECRET_KEYS:
            masked[key]["api_key_set"] = bool(secrets.get(key))
            masked[key].pop("api_key", None)
        return masked
