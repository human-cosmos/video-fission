from __future__ import annotations

from pathlib import Path

import pytest
from video_fission import providers


@pytest.mark.parametrize(
    ("duration_ms", "expected"),
    [
        (1000, (1, 4)),
        (3500, (4, 4)),
        (5000, (5, 5)),
        (15100, (16, 15)),
        (17103, (18, 15)),
    ],
)
def test_seedance_duration_is_clamped_to_supported_window(duration_ms, expected):
    assert providers.seedance_duration_seconds(duration_ms) == expected


def test_seedance_payload_uses_supported_duration():
    payload = providers.build_seedance_submit_payload(
        "seedance-2-0-fast",
        "test prompt",
        17103,
        1920,
        1080,
    )

    assert payload["duration"] == 15
    assert "input_contents" not in payload


def test_seedance_payload_includes_uploaded_reference_images(tmp_path: Path):
    image = tmp_path / "product.png"
    image.write_bytes(b"reference-image")

    payload = providers.build_seedance_submit_payload(
        "seedance-2-0-fast",
        "keep the product appearance",
        5000,
        1080,
        1920,
        reference_images=[image],
    )

    assert payload["input_contents"] == [
        {
            "type": "image_url",
            "role": "reference_image",
            "image_url": {"url": "data:image/png;base64,cmVmZXJlbmNlLWltYWdl"},
        }
    ]


def test_seedance_submission_adapts_long_shot_before_remote_call(monkeypatch, tmp_path: Path):
    submitted: list[dict | None] = []

    def request(url, key, *, timeout, payload=None):
        submitted.append(payload)
        if payload is not None:
            return {"data": {"task_id": "task_duration_15"}}
        return {
            "data": {
                "status": "completed",
                "result": {"video": {"url": "https://fake.invalid/video.mp4"}},
            }
        }

    monkeypatch.setattr(providers, "_request_seedance", request)
    monkeypatch.setattr(
        providers,
        "_download_seedance_video",
        lambda url, dest, *args: dest.write_bytes(b"video"),
    )

    dest = providers.generate_seedance_video(
        "https://fake.invalid",
        "test-key",
        "seedance-2-0-fast",
        "test prompt",
        17103,
        1920,
        1080,
        30,
        tmp_path / "clip.mp4",
    )

    assert submitted[0] is not None
    assert submitted[0]["duration"] == 15
    assert "input_contents" not in submitted[0]
    assert dest.read_bytes() == b"video"
