from __future__ import annotations

import json
import urllib.error
import urllib.request
from copy import deepcopy
from typing import Any

from . import media
from .db import Database

DEFAULT_SETTINGS: dict[str, Any] = {
    "multimodal": {
        "provider": "local_ffmpeg",
        "base_url": "",
        "api_key_set": False,
        "model": "",
        "timeout_seconds": 120,
    },
    "video_generator": {
        "provider": "local_ffmpeg",
        "base_url": "",
        "api_key_set": False,
        "model": "",
        "timeout_seconds": 300,
        "fallback_to_local": True,
    },
    "tts": {
        "provider": "none",
        "base_url": "",
        "api_key_set": False,
        "model": "",
        "voice": "alloy",
    },
    "compose": {
        "burn_in_subtitles": True,
        "mix_reference_bgm": True,
        "bgm_volume": 0.18,
    },
}

PROVIDER_ENUMS = {
    "multimodal": {"local_ffmpeg", "openai_compatible"},
    "video_generator": {"local_ffmpeg", "openai_compatible", "generic_http"},
    "tts": {"none", "openai_compatible"},
}

SECRET_KEYS = ("multimodal", "video_generator", "tts")


class SettingsStore:
    def __init__(self, db: Database):
        self.db = db

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
        for section in ("multimodal", "video_generator", "tts", "compose"):
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
            for field in ("base_url", "model", "voice"):
                if field in incoming and incoming[field] is not None and field in public[section]:
                    public[section][field] = str(incoming[field]).strip()
            if "timeout_seconds" in incoming and incoming["timeout_seconds"] is not None:
                timeout = int(incoming["timeout_seconds"])
                if timeout < 5 or timeout > 3600:
                    raise ValueError("timeout_seconds 需在 5 到 3600 之间")
                public[section]["timeout_seconds"] = timeout
            if (
                section == "video_generator"
                and "fallback_to_local" in incoming
                and incoming["fallback_to_local"] is not None
            ):
                public[section]["fallback_to_local"] = bool(incoming["fallback_to_local"])
            if section == "compose":
                if "burn_in_subtitles" in incoming and incoming["burn_in_subtitles"] is not None:
                    public[section]["burn_in_subtitles"] = bool(incoming["burn_in_subtitles"])
                if "mix_reference_bgm" in incoming and incoming["mix_reference_bgm"] is not None:
                    public[section]["mix_reference_bgm"] = bool(incoming["mix_reference_bgm"])
                if "bgm_volume" in incoming and incoming["bgm_volume"] is not None:
                    volume = float(incoming["bgm_volume"])
                    if volume < 0 or volume > 1:
                        raise ValueError("bgm_volume 需在 0 到 1 之间")
                    public[section]["bgm_volume"] = volume
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
            raise ValueError("target 必须是 multimodal、video_generator 或 tts")
        public, secrets = self._load(user_id)
        section = public[target]
        provider = section.get("provider") or "local_ffmpeg"
        if target == "tts" and provider == "none":
            return {"ok": True, "provider": "none", "detail": "TTS 未启用，出片时不会阻塞"}
        if provider == "local_ffmpeg":
            probe = media.ffmpeg_available()
            return {
                "ok": bool(probe.get("ok")),
                "provider": provider,
                "detail": probe.get("version") or probe.get("detail") or "ffmpeg 不可用",
            }
        key = secrets.get(target) or ""
        base_url = str(section.get("base_url") or "").rstrip("/")
        if not base_url:
            return {"ok": False, "provider": provider, "detail": "未配置 base_url"}
        if not key:
            return {"ok": False, "provider": provider, "detail": "未配置 API Key"}
        return self._probe_remote(
            base_url,
            key,
            str(section.get("model") or ""),
            int(section.get("timeout_seconds") or 30),
            provider,
        )

    def _probe_remote(self, base_url: str, api_key: str, model: str, timeout: int, provider: str) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        models_url = f"{base_url}/models"
        try:
            request = urllib.request.Request(models_url, headers=headers, method="GET")
            with urllib.request.urlopen(request, timeout=min(timeout, 20)) as response:
                payload = response.read(4000).decode("utf-8", errors="replace")
            if "sk-" in payload or api_key in payload:
                payload = "模型列表已返回"
            return {"ok": True, "provider": provider, "detail": f"已连通 {models_url}"}
        except Exception:
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
                with urllib.request.urlopen(request, timeout=min(timeout, 20)) as response:
                    response.read(2000)
                return {"ok": True, "provider": provider, "detail": f"已连通 {url}"}
            except urllib.error.HTTPError as exc:
                return {"ok": False, "provider": provider, "detail": f"探活失败 HTTP {exc.code}"}
            except Exception as exc:
                return {"ok": False, "provider": provider, "detail": f"探活失败：{exc.__class__.__name__}"}

    def _load(self, user_id: str | None = None) -> tuple[dict[str, Any], dict[str, str]]:
        row = self.db.get_user_settings(user_id) if user_id else self.db.get_app_settings()
        public = deepcopy(DEFAULT_SETTINGS)
        secrets = {key: "" for key in SECRET_KEYS}
        if not row:
            if user_id:
                self.db.upsert_user_settings(user_id, public, secrets)
            else:
                self.db.upsert_app_settings(public, secrets)
            return public, secrets
        stored = row.get("public") or {}
        for section, defaults in DEFAULT_SETTINGS.items():
            if isinstance(stored.get(section), dict):
                public[section] = {
                    **defaults,
                    **{
                        key: value
                        for key, value in stored[section].items()
                        if key in defaults and key != "api_key"
                    },
                }
        for key, value in (row.get("secrets") or {}).items():
            if key in secrets:
                secrets[key] = str(value or "")
        return public, secrets

    def _mask(self, public: dict[str, Any], secrets: dict[str, str]) -> dict[str, Any]:
        masked = deepcopy(public)
        for key in SECRET_KEYS:
            masked[key]["api_key_set"] = bool(secrets.get(key))
            masked[key].pop("api_key", None)
        return masked
