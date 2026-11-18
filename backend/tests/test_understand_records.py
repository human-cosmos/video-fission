from __future__ import annotations

import json
from pathlib import Path

from video_fission.understand import service as understand_service
from video_fission.understand.gateway import GeminiGatewayClient
from video_fission.understand.records import GeminiCallRecorder
from video_fission.understand.transitions import TransitionInfo


def test_gemini_call_recorder_writes_separate_json_files(tmp_path: Path):
    understanding = GeminiCallRecorder(
        tmp_path,
        "video_understanding",
        model="gemini-test",
    )
    clips = GeminiCallRecorder(
        tmp_path,
        "clip_analysis",
        model="gemini-test",
    )

    understanding_path = understanding.record(
        label="split_video",
        input_mode="video",
        status="succeeded",
        elapsed_ms=12.34,
        response={"code": 0, "data": {"video_summary": "完整视频"}},
    )
    clip_path = clips.record(
        label="clip_seg_01",
        input_mode="video",
        status="succeeded",
        elapsed_ms=5.67,
        response={"code": 0, "data": {"clip_description": "片段"}},
    )

    assert understanding_path.parent.name == "video_understanding"
    assert clip_path.parent.name == "clip_analysis"
    assert understanding_path != clip_path
    understanding_payload = json.loads(understanding_path.read_text(encoding="utf-8"))
    clip_payload = json.loads(clip_path.read_text(encoding="utf-8"))
    assert understanding_payload["call_type"] == "video_understanding"
    assert understanding_payload["response"]["data"]["video_summary"] == "完整视频"
    assert clip_payload["call_type"] == "clip_analysis"
    assert clip_payload["response"]["data"]["clip_description"] == "片段"
    assert not list(tmp_path.rglob("*.tmp"))


def test_gemini_call_recorder_records_errors(tmp_path: Path):
    recorder = GeminiCallRecorder(tmp_path, "video_understanding")

    output_path = recorder.record(
        label="split_video",
        input_mode="video",
        status="failed",
        elapsed_ms=100,
        error=TimeoutError("gateway timed out"),
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["error"] == {
        "type": "TimeoutError",
        "message": "gateway timed out",
    }
    assert "response" not in payload


def test_understanding_fallback_saves_each_gateway_response(monkeypatch, tmp_path: Path):
    video = tmp_path / "source.mp4"
    frame = tmp_path / "frame.jpg"
    video.write_bytes(b"video")
    frame.write_bytes(b"jpeg")
    calls: list[dict] = []

    def fake_call(_self, _prompt, **kwargs):
        calls.append(kwargs)
        if "video_path" in kwargs:
            return {"code": 0, "data": {}}
        return {
            "code": 0,
            "data": '{"video_lens":[{"start_time":0,"end_time":1}]}',
        }

    monkeypatch.setattr(GeminiGatewayClient, "call", fake_call)
    recorder = GeminiCallRecorder(tmp_path, "video_understanding", model="gemini-test")

    payload, used_frames = understand_service._call_understand(
        "gemini_gateway",
        {
            "base_url": "https://gateway.example/chat",
            "model": "gemini-test",
            "max_tokens": 12000,
        },
        "token",
        "分析",
        video,
        [frame],
        0.6,
        recorder=recorder,
        label="split",
    )

    assert used_frames is True
    assert payload["video_lens"][0]["start_time"] == 0
    assert len(calls) == 2
    paths = sorted(recorder.directory.glob("*.json"))
    assert len(paths) == 2
    records = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    assert {record["call_label"] for record in records} == {"split_video", "split_frames"}
    assert {record["input_mode"] for record in records} == {"video", "frames"}


def test_remote_split_saves_video_understanding_response(monkeypatch, tmp_path: Path):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"video")
    (tmp_path / "seg_01_frame_01.jpg").write_bytes(b"jpeg")
    raw_response = {
        "code": 0,
        "data": json.dumps(
            {
                "video_summary": "整片摘要",
                "video_lens": [
                    {
                        "start_time": 0,
                        "end_time": 4,
                        "slot": ["开场钩子"],
                        "summary": "开场",
                    },
                    {
                        "start_time": 4,
                        "end_time": 8.1,
                        "slot": ["响应号召"],
                        "summary": "结尾",
                    },
                ],
            },
            ensure_ascii=False,
        ),
    }

    monkeypatch.setattr(GeminiGatewayClient, "call", lambda *_args, **_kwargs: raw_response)
    monkeypatch.setattr(
        understand_service,
        "ensure_under_size",
        lambda source, *_args, **_kwargs: (source, ""),
    )

    lenses, tags, provider, _model, _warning, _opening = understand_service._remote_split(
        video,
        tmp_path,
        8.1,
        {
            "base_url": "https://gateway.example/chat",
            "model": "gemini-test",
            "max_tokens": 12000,
        },
        "token",
        "gemini_gateway",
        {"gemini_max_size_mb": 15},
        TransitionInfo(),
        None,
    )

    assert provider == "gemini_gateway"
    assert len(lenses) == 2
    assert tags["video_summary"] == "整片摘要"
    paths = list((tmp_path / "gemini_api_calls" / "video_understanding").glob("*.json"))
    assert len(paths) == 1
    record = json.loads(paths[0].read_text(encoding="utf-8"))
    assert record["call_label"] == "split_video"
    assert record["response"] == raw_response


def test_clip_analysis_saves_gateway_response(monkeypatch, tmp_path: Path):
    clip = tmp_path / "clips" / "seg_01.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"video")
    raw_response = {
        "code": 0,
        "data": {
            "is_include_product": "yes",
            "clip_description": "人物展示商品",
        },
    }

    monkeypatch.setattr(
        GeminiGatewayClient,
        "call",
        lambda *_args, **_kwargs: raw_response,
    )
    segments = [
        {
            "id": "seg_01",
            "start_ms": 0,
            "end_ms": 1000,
            "clip_path": "clips/seg_01.mp4",
        }
    ]

    understand_service._analyze_clips(
        segments,
        tmp_path,
        {
            "base_url": "https://gateway.example/chat",
            "model": "gemini-test",
            "max_tokens": 12000,
        },
        "token",
        "gemini_gateway",
        {"clip_workers": 1},
        {},
        [],
        lambda *_args, **_kwargs: None,
        None,
    )

    paths = list((tmp_path / "gemini_api_calls" / "clip_analysis").glob("*.json"))
    assert len(paths) == 1
    record = json.loads(paths[0].read_text(encoding="utf-8"))
    assert record["call_label"] == "clip_seg_01_video"
    assert record["input_mode"] == "video"
    assert record["response"] == raw_response
    assert segments[0]["lens_info"]["clip_description"] == "人物展示商品"


def test_record_write_failure_does_not_break_understanding(monkeypatch, tmp_path: Path):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"video")
    recorder = GeminiCallRecorder(tmp_path, "video_understanding")

    def fail_record(**_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(recorder, "record", fail_record)
    monkeypatch.setattr(
        GeminiGatewayClient,
        "call",
        lambda *_args, **_kwargs: {
            "code": 0,
            "data": '{"video_lens":[{"start_time":0,"end_time":1}]}',
        },
    )

    payload, used_frames = understand_service._call_understand(
        "gemini_gateway",
        {
            "base_url": "https://gateway.example/chat",
            "model": "gemini-test",
            "max_tokens": 12000,
        },
        "token",
        "分析",
        video,
        [],
        0.6,
        recorder=recorder,
        label="split",
    )

    assert used_frames is False
    assert payload["video_lens"][0]["end_time"] == 1
