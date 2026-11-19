from __future__ import annotations

from dataclasses import replace

from video_fission.db import Database
from video_fission.settings_store import SettingsStore
from video_fission.understand.gateway import GeminiGatewayClient
from video_fission.understand.service import (
    GEMINI_CLIP_TIMEOUT_SECONDS,
    GEMINI_SPLIT_TIMEOUT_SECONDS,
)


def test_gemini_timeouts_default_to_360_seconds(test_settings):
    runtime = replace(
        test_settings,
        gemini_api_url="https://gemini.example/v1",
        gemini_model="gemini-test",
    )
    db = Database(runtime.database_path)
    db.initialize()
    store = SettingsStore(db, runtime)

    assert store.public_settings()["multimodal"]["timeout_seconds"] == 360
    assert GEMINI_SPLIT_TIMEOUT_SECONDS == 360
    assert GEMINI_CLIP_TIMEOUT_SECONDS == 360
    assert GeminiGatewayClient(base_url=runtime.gemini_api_url).timeout_seconds == 360


def test_runtime_gemini_default_migrates_old_120_second_timeout(test_settings):
    runtime = replace(
        test_settings,
        gemini_api_url="https://gemini.example/v1",
        gemini_model="gemini-test",
    )
    db = Database(runtime.database_path)
    db.initialize()
    store = SettingsStore(db, runtime)
    stored_multimodal = {
        **store._defaults()["multimodal"],
        "timeout_seconds": 120,
    }
    db.upsert_app_settings({"multimodal": stored_multimodal}, {})

    assert store.public_settings()["multimodal"]["timeout_seconds"] == 360
    assert db.get_app_settings()["public"]["multimodal"]["timeout_seconds"] == 360
