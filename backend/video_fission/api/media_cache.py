from __future__ import annotations

from collections.abc import Mapping

MUTABLE_MEDIA_CACHE_CONTROL = "private, no-cache"
IMMUTABLE_MEDIA_CACHE_CONTROL = "private, max-age=31536000, immutable"


def media_cache_headers(*, immutable: bool = False) -> dict[str, str]:
    return {
        "Cache-Control": (
            IMMUTABLE_MEDIA_CACHE_CONTROL
            if immutable
            else MUTABLE_MEDIA_CACHE_CONTROL
        ),
        "Vary": "Authorization, Cookie",
    }


def _normalized_etag(value: str) -> str:
    value = value.strip()
    return value[2:] if value.startswith("W/") else value


def media_not_modified_headers(
    if_none_match: str,
    response_headers: Mapping[str, str],
) -> dict[str, str] | None:
    etag = response_headers.get("etag", "").strip()
    cache_control = response_headers.get("cache-control", "")
    if not etag or "no-store" in cache_control.lower():
        return None
    candidates = {
        _normalized_etag(value)
        for value in if_none_match.split(",")
        if value.strip()
    }
    if "*" not in candidates and _normalized_etag(etag) not in candidates:
        return None
    excluded = {
        "content-length",
        "content-range",
        "content-encoding",
        "transfer-encoding",
        "accept-ranges",
    }
    return {
        key: value
        for key, value in response_headers.items()
        if key.lower() not in excluded
    }
