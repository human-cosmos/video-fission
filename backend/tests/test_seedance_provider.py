from __future__ import annotations

import json
from pathlib import Path
from threading import Event

from video_fission import providers


class FakeResponse:
    def __init__(self, body: bytes, content_type: str = "application/json"):
        self._body = body
        self.headers = {"Content-Type": content_type}

    def read(self, _size: int = -1) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_seedance_submit_poll_and_download(monkeypatch, tmp_path: Path):
    requests = []
    responses = [
        FakeResponse(
            json.dumps(
                {
                    "code": 0,
                    "bizCode": 0,
                    "msg": "ok",
                    "data": {
                        "task_id": "task-123",
                        "status_url": "/acore/model/v1/task/video/task-123",
                        "estimated_time": 20,
                    },
                }
            ).encode()
        ),
        FakeResponse(
            json.dumps(
                {
                    "code": 0,
                    "bizCode": 0,
                    "msg": "ok",
                    "data": {
                        "task_id": "task-123",
                        "status": "completed",
                        "result": {
                            "video": {
                                "url": "https://cdn.example.test/video.mp4",
                                "duration": 3,
                                "format": "mp4",
                            }
                        },
                    },
                }
            ).encode()
        ),
        FakeResponse(b"\x00\x00\x00\x18ftypmp42test-video", "video/mp4"),
    ]

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return responses.pop(0)

    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)
    dest = tmp_path / "shot_01.mp4"
    result = providers.generate_remote_video(
        "seedance",
        "https://api.giikin.com",
        "dk-ai-p-test",
        "seedance-2.0-flash",
        "竖屏展示一只白色吹风机",
        2700,
        1080,
        1920,
        30,
        120,
        dest,
        source="video-fission-test",
        resolution="720p",
    )

    assert result == dest
    assert dest.read_bytes().endswith(b"test-video")
    assert [request.full_url for request, _timeout in requests] == [
        "https://api.giikin.com/acore/model/v1/task/generate/video",
        "https://api.giikin.com/acore/model/v1/task/video/task-123",
        "https://cdn.example.test/video.mp4",
    ]
    submit = json.loads(requests[0][0].data.decode())
    assert submit["model"] == "seedance-2-0-fast"
    assert submit["duration"] == 3
    assert submit["aspect_ratio"] == "9:16"
    assert submit["options"]["resolution"] == "720p"
    assert submit["source"] == "video-fission-test"
    assert requests[0][0].headers["Authorization"] == "Bearer dk-ai-p-test"
    assert "Authorization" not in requests[2][0].headers


def test_seedance_video_url_supports_async_response_body():
    task_data = {
        "status": "completed",
        "responseBody": json.dumps(
            {
                "data": {
                    "video_url": "https://cdn.example.test/from-body.mp4",
                }
            }
        ),
    }
    assert providers._seedance_video_url(task_data) == "https://cdn.example.test/from-body.mp4"


def test_seedance_probe_reports_authenticated_endpoint(monkeypatch):
    def fake_urlopen(request, timeout):
        assert request.full_url.endswith("/acore/model/v1/models")
        return FakeResponse(
            json.dumps(
                {
                    "object": "list",
                    "data": [
                        {"id": "seedance-2-0-fast", "object": "model"},
                    ],
                }
            ).encode()
        )

    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)
    result = providers.probe_seedance(
        "https://api.giikin.com",
        "dk-ai-p-test",
        10,
        "seedance-2.0-flash",
    )
    assert result["ok"] is True
    assert result["provider"] == "seedance"
    assert "seedance-2-0-fast" in result["detail"]


def test_seedance_poll_stops_when_cancelled(monkeypatch, tmp_path: Path):
    cancel = Event()
    calls = 0

    def fake_urlopen(request, timeout):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise AssertionError("cancelled task must not issue a status request")
        cancel.set()
        return FakeResponse(
            json.dumps(
                {
                    "code": 200,
                    "bizCode": 3001000,
                    "msg": "提交成功",
                    "data": {
                        "task_id": "task-cancelled",
                        "status_url": "/api/v1/tasks/task-cancelled",
                    },
                }
            ).encode()
        )

    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)
    try:
        providers.generate_seedance_video(
            "https://api.giikin.com",
            "dk-ai-p-test",
            "seedance-2-0-fast",
            "test",
            1000,
            1080,
            1920,
            30,
            tmp_path / "clip.mp4",
            cancel=cancel,
        )
    except providers.ProviderCancelled:
        pass
    else:
        raise AssertionError("expected ProviderCancelled")


def test_seedance_endpoint_accepts_root_and_model_prefix():
    path = providers.SEEDANCE_MODELS_PATH
    assert providers._seedance_endpoint("https://api.giikin.com", path) == (
        "https://api.giikin.com/acore/model/v1/models"
    )
    assert providers._seedance_endpoint("https://api.giikin.com/acore", path) == (
        "https://api.giikin.com/acore/model/v1/models"
    )
    assert providers._seedance_endpoint("https://api.giikin.com/acore/model/v1", path) == (
        "https://api.giikin.com/acore/model/v1/models"
    )
