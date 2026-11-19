from __future__ import annotations

import io
import json
import threading
import time
import urllib.error
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from video_fission.db import Database
from video_fission.settings_store import SettingsStore
from video_fission.understand import gateway as gateway_module
from video_fission.understand import service as understand_service
from video_fission.understand.gateway import GatewayError, GeminiGatewayClient, video_mime
from video_fission.understand.normalize import coverage_ok_lenses


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_gemini_gateway_sends_correct_mime_and_source(monkeypatch, tmp_path: Path):
    video = tmp_path / "source.mov"
    video.write_bytes(b"mov-data")
    captured: dict = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return _FakeResponse({"code": 0, "data": '{"ok": true}'})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = GeminiGatewayClient(
        base_url="https://gateway.example/chat",
        api_key="shared-token",
        model="gemini-test",
        source="video-fission",
        timeout_seconds=30,
    )
    response = client.call("分析视频", video_path=video)

    assert response == {"code": 0, "data": '{"ok": true}'}
    request = captured["request"]
    payload = json.loads(request.data.decode("utf-8"))
    parts = payload["messages"][0]["parts"]
    assert parts[0]["inlineData"]["mimeType"] == "video/quicktime"
    assert payload["source"] == "video-fission"
    assert payload["max_tokens"] == 30720
    assert request.headers["Authorization"] == "shared-token"
    assert 29.5 <= captured["timeout"] <= 30


def test_gemini_gateway_keeps_configured_budget_for_text(monkeypatch):
    captured: dict = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse({"code": 0, "data": '{"ok": true}'})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = GeminiGatewayClient(
        base_url="https://gateway.example/chat",
        api_key="shared-token",
        max_tokens=12000,
    )

    client.call("仅返回 JSON", max_tokens=64)

    assert captured["payload"]["max_tokens"] == 64


def test_video_mime_defaults_for_unknown_extensions():
    assert video_mime("clip.webm") == "video/webm"
    assert video_mime("clip.unknown") == "video/mp4"


def test_multimodal_default_stays_local_and_accepts_gateway_settings(test_settings):
    database = Database(test_settings.database_path)
    database.initialize()
    store = SettingsStore(database)

    public = store.public_settings()
    assert public["multimodal"]["provider"] == "local_ffmpeg"
    assert public["multimodal"]["upload_url"] == ""
    assert public["multimodal"]["token_url"] == ""
    assert public["multimodal"]["source"] == ""
    assert public["multimodal"]["max_tokens"] == 12000

    updated = store.update(
        {
            "multimodal": {
                "provider": "gemini_gateway",
                "base_url": "https://gateway.example/chat",
                "source": "video-fission",
                "model": "gemini-test",
            }
        }
    )
    assert updated["multimodal"]["provider"] == "gemini_gateway"
    assert updated["multimodal"]["source"] == "video-fission"


def test_gateway_environment_seeds_and_migrates_settings(test_settings):
    database = Database(test_settings.database_path)
    database.initialize()
    database.upsert_app_settings(
        {
            "multimodal": {
                "provider": "local_ffmpeg",
                "base_url": "",
                "model": "",
                "timeout_seconds": 120,
            }
        },
        {"multimodal": ""},
    )
    runtime = replace(
        test_settings,
        gemini_api_url="https://gateway.example/chat",
        gemini_upload_url="https://gateway.example/upload",
        gemini_token_url="https://gateway.example/token",
        gemini_model="gemini-3.8-flash",
        gemini_source="hot-product-system",
    )
    store = SettingsStore(database, runtime)

    public = store.public_settings()
    persisted = database.get_app_settings()["public"]

    assert public["multimodal"]["provider"] == "gemini_gateway"
    assert public["multimodal"]["model"] == "gemini-3.8-flash"
    assert public["multimodal"]["upload_url"] == "https://gateway.example/upload"
    assert public["multimodal"]["max_tokens"] == 12000
    assert public["multimodal"]["timeout_seconds"] == 120
    assert persisted["multimodal"]["provider"] == "gemini_gateway"


def test_gateway_environment_migrates_seeded_timeout_to_120(test_settings):
    database = Database(test_settings.database_path)
    database.initialize()
    runtime = replace(
        test_settings,
        gemini_api_url="https://gateway.example/chat",
        gemini_upload_url="https://gateway.example/upload",
        gemini_token_url="https://gateway.example/token",
        gemini_model="gemini-3.8-flash",
        gemini_source="hot-product-system",
    )
    database.upsert_app_settings(
        {
            "multimodal": {
                "provider": "gemini_gateway",
                "base_url": runtime.gemini_api_url,
                "upload_url": runtime.gemini_upload_url,
                "token_url": runtime.gemini_token_url,
                "model": runtime.gemini_model,
                "source": runtime.gemini_source,
                "max_tokens": 12000,
                "timeout_seconds": 360,
            }
        },
        {"multimodal": ""},
    )

    public = SettingsStore(database, runtime).public_settings()
    stored = database.get_app_settings()["public"]

    assert public["multimodal"]["timeout_seconds"] == 120
    assert stored["multimodal"]["timeout_seconds"] == 120


def test_gateway_extracts_nested_content_response():
    client = GeminiGatewayClient(base_url="https://gateway.example/chat", api_key="token")
    response = {
        "code": 0,
        "data": {
            "content": json.dumps(
                {
                    "video_lens": [{"start_time": 0, "end_time": 1, "tag_level1": ["开场钩子"]}],
                    "video_summary": "ok",
                }
            )
        },
    }

    payload = client.extract_payload(response)

    assert payload["video_summary"] == "ok"
    assert payload["video_lens"][0]["tag_level1"] == ["开场钩子"]


def test_gateway_probe_rejects_empty_model_payload(monkeypatch):
    client = GeminiGatewayClient(base_url="https://gateway.example/chat", api_key="token")
    monkeypatch.setattr(client, "call", lambda *_args, **_kwargs: {"code": 0, "data": {}})

    assert client.probe()["ok"] is False

    monkeypatch.setattr(
        client,
        "call",
        lambda *_args, **_kwargs: {"code": 0, "data": '{"ok": true}'},
    )
    assert client.probe()["ok"] is True


def test_gateway_timeout_error_keeps_actionable_detail(monkeypatch):
    def fake_urlopen(_request, timeout):
        raise TimeoutError(f"read timed out after {timeout:.0f}s")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = GeminiGatewayClient(
        base_url="https://gateway.example/chat",
        api_key="token",
        timeout_seconds=5,
        max_retries=0,
    )

    with pytest.raises(GatewayError, match="read timed out"):
        client.call("分析视频")


def test_gateway_http_error_keeps_safe_upstream_diagnostics(monkeypatch):
    response_body = {
        "code": 400,
        "message": "Invalid request. Check your parameters and try again.",
        "error_type": "upstream_http_error",
        "request_id": "req-upstream-123",
        "details": {
            "upstream_response_text": json.dumps(
                {
                    "error": {
                        "message": "Invalid request. Check your parameters and try again.",
                    }
                }
            ),
            "upstream_request_body": "sensitive prompt and video URI",
        },
    }

    def fake_urlopen(_request, timeout):
        raise urllib.error.HTTPError(
            "https://gateway.example/chat",
            400,
            "Bad Request",
            hdrs=None,
            fp=io.BytesIO(json.dumps(response_body).encode("utf-8")),
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = GeminiGatewayClient(
        base_url="https://gateway.example/chat",
        api_key="token",
        max_retries=0,
    )

    with pytest.raises(GatewayError) as captured:
        client.call("分析视频")

    detail = str(captured.value)
    assert "HTTP 400" in detail
    assert "Invalid request" in detail
    assert "upstream_http_error" in detail
    assert "req-upstream-123" in detail
    assert "sensitive prompt" not in detail


def test_gateway_prefers_uploaded_file_uri(monkeypatch, tmp_path: Path):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"video")
    captured: dict = {}

    def fake_post(url, **kwargs):
        captured["upload_url"] = url
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "code": 200,
                "data": {"gsutil_uri": "https://storage.example/source.mp4"},
            },
        )

    def fake_urlopen(request, timeout):
        captured["request"] = request
        return _FakeResponse({"code": 0, "data": '{"ok": true}'})

    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = GeminiGatewayClient(
        base_url="https://gateway.example/chat",
        upload_url="https://gateway.example/upload",
        api_key="token",
        model="gemini-3.8-flash",
    )

    client.call("分析视频", video_path=video)

    payload = json.loads(captured["request"].data.decode("utf-8"))
    file_data = payload["messages"][0]["parts"][0]["fileData"]
    assert captured["upload_url"] == "https://gateway.example/upload"
    assert file_data["fileUri"] == "https://storage.example/source.mp4"


def test_gateway_retries_inline_when_file_uri_returns_400(monkeypatch, tmp_path: Path):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"video")
    captured: list[dict] = []

    def fake_post(_url, **_kwargs):
        return SimpleNamespace(
            raise_for_status=lambda: None,
            status_code=200,
            json=lambda: {
                "code": 200,
                "data": {"gsutil_uri": "https://storage.example/source.mp4"},
            },
        )

    def fake_urlopen(request, timeout):
        payload = json.loads(request.data.decode("utf-8"))
        captured.append(payload)
        if len(captured) == 1:
            raise urllib.error.HTTPError(
                request.full_url,
                400,
                "Bad Request",
                hdrs=None,
                fp=io.BytesIO(
                    json.dumps(
                        {
                            "code": 400,
                            "message": "Invalid request",
                            "error_type": "upstream_http_error",
                        }
                    ).encode("utf-8")
                ),
            )
        return _FakeResponse({"code": 0, "data": '{"ok": true}'})

    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = GeminiGatewayClient(
        base_url="https://gateway.example/chat",
        upload_url="https://gateway.example/upload",
        api_key="token",
    )

    response = client.call("分析视频", video_path=video)

    assert response == {"code": 0, "data": '{"ok": true}'}
    assert len(captured) == 2
    assert captured[0]["messages"][0]["parts"][0]["fileData"]["fileUri"].endswith("source.mp4")
    assert "inlineData" in captured[1]["messages"][0]["parts"][0]


def test_gateway_does_not_inline_large_file_uri_failure(monkeypatch, tmp_path: Path):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"x" * (4 * 1024 * 1024 + 1))
    captured: list[dict] = []

    def fake_post(_url, **_kwargs):
        return SimpleNamespace(
            raise_for_status=lambda: None,
            status_code=200,
            json=lambda: {
                "code": 200,
                "data": {"gsutil_uri": "https://storage.example/source.mp4"},
            },
        )

    def fake_urlopen(request, timeout):
        captured.append(json.loads(request.data.decode("utf-8")))
        raise urllib.error.HTTPError(
            request.full_url,
            400,
            "Bad Request",
            hdrs=None,
            fp=io.BytesIO(
                json.dumps(
                    {
                        "code": 400,
                        "message": "Invalid request",
                        "error_type": "upstream_http_error",
                    }
                ).encode("utf-8")
            ),
        )

    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = GeminiGatewayClient(
        base_url="https://gateway.example/chat",
        upload_url="https://gateway.example/upload",
        api_key="token",
        max_retries=0,
    )

    with pytest.raises(GatewayError):
        client.call("分析视频", video_path=video)

    assert len(captured) == 1
    assert "fileData" in captured[0]["messages"][0]["parts"][0]


def test_gateway_limits_concurrent_http_requests(monkeypatch):
    monkeypatch.setattr(
        gateway_module,
        "_GEMINI_REQUEST_SLOTS",
        threading.BoundedSemaphore(2),
    )
    release = threading.Event()
    start = threading.Barrier(4)
    lock = threading.Lock()
    active = 0
    peak = 0
    errors: list[Exception] = []

    def fake_urlopen(_request, timeout):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        release.wait(2)
        with lock:
            active -= 1
        return _FakeResponse({"code": 0, "data": '{"ok": true}'})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    clients = [
        GeminiGatewayClient(
            base_url="https://gateway.example/chat",
            api_key="token",
            max_retries=0,
        )
        for _ in range(4)
    ]

    def worker(client: GeminiGatewayClient) -> None:
        try:
            start.wait()
            client.call("ping")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(client,)) for client in clients]
    for thread in threads:
        thread.start()
    time.sleep(0.25)
    release.set()
    for thread in threads:
        thread.join(timeout=2)

    assert peak == 2
    assert errors == []


def test_gateway_uploads_audio_with_audio_mime_and_custom_object_name(monkeypatch, tmp_path: Path):
    audio = tmp_path / "reference-vocals.m4a"
    audio.write_bytes(b"audio")
    captured: dict = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["files"] = kwargs["files"]
        return SimpleNamespace(
            raise_for_status=lambda: None,
            status_code=200,
            json=lambda: {
                "code": 200,
                "data": {
                    "public_url": "https://public.example/reference-vocals.m4a",
                    "gsutil_uri": "https://storage.example/reference-vocals.m4a",
                },
            },
        )

    monkeypatch.setattr("httpx.post", fake_post)
    client = GeminiGatewayClient(
        base_url="",
        upload_url="https://upload.example.test/gcloud",
        api_key="token",
    )

    uri = client.upload_media(
        audio,
        "token",
        object_name="prd_test-reference-vocals.m4a",
    )

    upload_name, _handle, mime = captured["files"]["file"]
    assert upload_name == "prd_test-reference-vocals.m4a"
    assert mime == "audio/mp4"
    assert uri == "https://public.example/reference-vocals.m4a"


def test_invalid_video_response_falls_back_to_frames(monkeypatch, tmp_path: Path):
    video = tmp_path / "source.mp4"
    frame = tmp_path / "frame.jpg"
    video.write_bytes(b"video")
    frame.write_bytes(b"jpeg")
    calls: list[dict] = []

    def fake_call(_self, _prompt, **kwargs):
        calls.append(kwargs)
        if "video_path" in kwargs:
            return {"code": 0, "data": {}}
        return {"code": 0, "data": '{"video_lens":[{"start_time":0,"end_time":1}]}'}

    monkeypatch.setattr(GeminiGatewayClient, "call", fake_call)
    payload, used_frames = understand_service._call_understand(
        "gemini_gateway",
        {
            "base_url": "https://gateway.example/chat",
            "model": "gemini-3.8-flash",
            "max_tokens": 12000,
        },
        "token",
        "分析",
        video,
        [frame],
        0.6,
    )

    assert used_frames is True
    assert payload["video_lens"][0]["start_time"] == 0
    assert "video_path" in calls[0]
    assert "frames" in calls[1]


def test_understand_request_returns_when_cancelled(monkeypatch, tmp_path: Path):
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"jpeg")
    cancel = threading.Event()

    def blocked_call(_self, _prompt, **_kwargs):
        time.sleep(5)
        return {"code": 0, "data": "{}"}

    monkeypatch.setattr(GeminiGatewayClient, "call", blocked_call)
    timer = threading.Timer(0.2, cancel.set)
    timer.start()
    started = time.monotonic()
    try:
        understand_service._call_understand(
            "gemini_gateway",
            {
                "base_url": "https://gateway.example/chat",
                "model": "gemini-3.8-flash",
                "max_tokens": 12000,
            },
            "token",
            "分析",
            None,
            [frame],
            0.6,
            cancel=cancel,
        )
    except Exception as exc:
        assert exc.__class__.__name__ == "PipelineCancelled"
    else:
        raise AssertionError("cancelled request must not return normally")
    finally:
        timer.cancel()

    assert time.monotonic() - started < 1.5


def test_model_lens_coverage_is_checked_before_normalization():
    contiguous = [
        {"start_time": 0.0, "end_time": 3.0},
        {"start_time": 3.0, "end_time": 8.0},
    ]
    gap = [
        {"start_time": 0.0, "end_time": 3.0},
        {"start_time": 5.0, "end_time": 8.0},
    ]

    assert coverage_ok_lenses(contiguous, 8.0) is True
    assert coverage_ok_lenses(gap, 8.0) is False
