from __future__ import annotations

import os

import pytest
from video_fission.providers import probe_seedance
from video_fission.understand.gateway import GeminiGatewayClient

LIVE = os.getenv("RUN_LIVE_PROVIDERS", "").strip() == "1"


@pytest.mark.skipif(not LIVE, reason="set RUN_LIVE_PROVIDERS=1 to call live providers")
def test_live_gemini_gateway_probe():
    base_url = os.getenv("VF_GEMINI_API_URL", "").strip()
    if not base_url:
        pytest.skip("VF_GEMINI_API_URL is not configured")
    client = GeminiGatewayClient(
        base_url=base_url,
        upload_url=os.getenv("VF_GEMINI_UPLOAD_URL", "").strip(),
        token_url=os.getenv("VF_GEMINI_TOKEN_URL", "").strip(),
        api_key=os.getenv("VF_GEMINI_API_KEY", "").strip(),
        model=os.getenv("VF_GEMINI_MODEL", "").strip(),
        source=os.getenv("VF_GEMINI_SOURCE", "").strip(),
        timeout_seconds=60,
        max_retries=0,
    )

    result = client.probe()

    assert result["ok"] is True, result


@pytest.mark.skipif(not LIVE, reason="set RUN_LIVE_PROVIDERS=1 to call live providers")
def test_live_seedance_probe():
    base_url = os.getenv("VF_LIVE_SEEDANCE_BASE_URL", "").strip()
    api_key = os.getenv("VF_LIVE_SEEDANCE_API_KEY", "").strip()
    if not base_url or not api_key:
        pytest.skip("VF_LIVE_SEEDANCE_BASE_URL and VF_LIVE_SEEDANCE_API_KEY are required")

    result = probe_seedance(
        base_url,
        api_key,
        timeout=30,
        model=os.getenv("VF_LIVE_SEEDANCE_MODEL", "seedance-2-0-fast").strip(),
    )

    assert result["ok"] is True, result
