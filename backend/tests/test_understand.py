from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from video_fission.container import configure
from video_fission.conversation_cards import cards_for, observe_payload, stable_item_id, understanding_payload
from video_fission.main import app
from video_fission.understand.normalize import (
    SLOT_TO_ROLE,
    build_compact_split_prompt,
    build_split_prompt,
    coverage_ok,
    coverage_ok_lenses,
    extract_video_tags,
    normalize_segments,
    normalize_tags,
    role_from_slot,
    to_production_segments,
    validate_segments,
)
from video_fission.understand.observe import normalize_observe_response
from video_fission.understand.transitions import TransitionDetector, TransitionInfo

from tests.helpers import finish_production

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample-reference.mp4"


@pytest.fixture
def client(test_settings):
    configure(test_settings)
    with TestClient(app) as test_client:
        yield test_client


def test_normalize_tags_accepts_legacy_and_new_fields():
    slot, sub, desc = normalize_tags(
        {"tag_level1": "开场钩子，痛点具象", "tag_level2": ["结果钩子"], "tag_level3": "特写,收束,多余,第四项"}
    )
    assert slot == ["开场钩子", "痛点具象"]
    assert sub == ["结果钩子"]
    assert desc == ["特写", "收束", "多余"]
    again = normalize_tags({"slot": slot, "sub_slot": sub, "sub_slot_desc": desc})
    assert again == (slot, sub, desc)


def test_role_mapping_is_fixed():
    expected = {
        "开场钩子": "hook",
        "痛点具象": "value",
        "方案登场": "value",
        "卖点展示": "value",
        "效果展示": "value",
        "场景演示": "value",
        "信任构建": "value",
        "响应号召": "cta",
        "其他": "other",
        "": "other",
    }
    assert SLOT_TO_ROLE["开场钩子"] == "hook"
    for slot, role in expected.items():
        assert role_from_slot([slot] if slot else []) == role


def test_extract_video_tags_and_segment_validation():
    data = {
        "video_describe": " 一条短片 ",
        "video_summary": "卖点展示",
        "product_name": "香酥虾,香酥虾",
        "data": {"ignored": True},
        "video_lens": [
            {
                "description": "开场",
                "summary": "钩子",
                "start_time": "00:00",
                "end_time": 2.5,
                "tag_level1": "开场钩子",
            },
            {"description": "中间", "summary": "卖点", "start_time": 2.4, "end_time": "8.0", "slot": ["卖点展示"]},
            {"description": "结尾", "summary": "促销", "start_time": 7.8, "end_time": 8.1, "tag_level1": "响应号召"},
        ],
    }
    tags = extract_video_tags(data)
    assert tags["video_describe"] == "一条短片"
    assert tags["product_name"] == ["香酥虾"]
    segments = validate_segments(normalize_segments(data["video_lens"], 8.1), 8.1, fps=25)
    assert segments[0]["start_time"] == 0.0
    assert segments[-1]["end_time"] == 8.1
    for left, right in zip(segments, segments[1:], strict=False):
        assert right["start_time"] >= left["end_time"]
    production = to_production_segments(segments, 8100, "prd_demo")
    assert coverage_ok(production, 8100)
    assert [item["role"] for item in production] == ["hook", "value", "cta"]
    assert production[1]["reuse_recommended"] is True
    assert production[0]["reuse_recommended"] is False
    assert production[0]["clip_path"] == "clips/seg_01.mp4"


def test_model_segment_ids_cannot_escape_artifact_paths():
    segments = to_production_segments(
        [
            {
                "id": "../../escape",
                "start_time": 0,
                "end_time": 1,
                "slot": ["开场钩子"],
                "summary": "untrusted",
            },
            {
                "id": "../duplicate",
                "start_time": 1,
                "end_time": 2,
                "slot": ["响应号召"],
                "summary": "untrusted",
            },
        ],
        2000,
        "prd_demo",
    )
    assert [item["id"] for item in segments] == ["seg_01", "seg_02"]
    assert [item["clip_path"] for item in segments] == [
        "clips/seg_01.mp4",
        "clips/seg_02.mp4",
    ]


def test_refine_segments_snaps_to_synthetic_scores():
    info = TransitionInfo(
        fps=25.0,
        duration=8.1,
        total_frames=203,
        frame_scores=[0.0] * 203,
        cnn_scores=[0.0] * 203,
        ok=True,
    )
    # 2.7s and 5.4s at 25fps = frame 67.5 / 135
    info.frame_scores[68] = 80
    info.frame_scores[135] = 80
    raw = [
        {"description": "a", "summary": "a", "start_time": 0, "end_time": 3, "slot": ["开场钩子"]},
        {"description": "b", "summary": "b", "start_time": 3, "end_time": 6, "slot": ["卖点展示"]},
        {"description": "c", "summary": "c", "start_time": 6, "end_time": 8.1, "slot": ["响应号召"]},
    ]
    refined = TransitionDetector().refine_segments(info, raw)
    assert refined[0]["start_time"] == 0.0
    assert abs(refined[0]["end_time"] - 2.72) <= 0.08
    assert abs(refined[1]["end_time"] - 5.4) <= 0.12
    assert refined[-1]["end_time"] == 8.1
    for left, right in zip(refined, refined[1:], strict=False):
        assert right["start_time"] >= left["end_time"]


def test_observe_normalization_builds_claims_and_trims_questions():
    payload = {
        "shot_observation_card": [
            {
                "shot_id": 1,
                "visual_subjects": "虾",
                "raw_claim_id": "c1",
                "raw_claim": "外酥里嫩",
                "raw_claim_source": "speech",
                "raw_claim_scope": "shot",
                "raw_claim_language": "zh",
                "review_questions": ["q1", "q2", "q3"],
                "evidence_coverage": {"visual": "full", "audio": "partial", "notes": ""},
            }
        ],
        "speech_evidence": {
            "status": "used",
            "source": "clip",
            "language": "zh",
            "segments": [{"text": "外酥里嫩", "start": 0, "end": 1.2}],
            "notes": "",
        },
    }
    result = normalize_observe_response(payload, 2.0, has_audio=True, original_start=12.5)
    card = result["shot_observation_card"][0]
    assert card["visual_subjects"] == "虾"
    assert card["review"] == {"status": "not_requested"}
    assert card["review_questions"] == ["q1", "q2"]
    assert "复查问题超过 2 条" in card["uncertainty_notes"]
    assert result["raw_claims"][0]["raw_claim"] == "外酥里嫩"
    assert result["speech_evidence"]["segments"][0]["text"] == "外酥里嫩"
    assert card["start_time"] == 12.5
    assert card["end_time"] == 14.5
    assert result["raw_claims"][0]["start_time"] == 12.5
    assert result["raw_claims"][0]["end_time"] == 14.5
    assert result["speech_evidence"]["segments"][0]["start"] == 12.5
    assert result["speech_evidence"]["segments"][0]["end"] == 13.7


def test_settings_exposes_understand_and_gemini_gateway(client: TestClient):
    body = client.get("/api/settings").json()
    assert body["understand"]["transition_detection"] is True
    assert body["understand"]["clip_workers"] == 3
    assert body["understand"]["observe_timeout_seconds"] == 360
    assert body["multimodal"]["token_url"] == ""
    updated = client.put(
        "/api/settings",
        json={
            "multimodal": {"provider": "gemini_gateway", "base_url": "https://example.invalid/v1"},
            "understand": {
                "observe": False,
                "clip_workers": 2,
                "gemini_max_size_mb": 10,
                "observe_timeout_seconds": 480,
            },
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["multimodal"]["provider"] == "gemini_gateway"
    assert updated.json()["understand"]["observe"] is False
    assert updated.json()["understand"]["clip_workers"] == 2
    assert updated.json()["understand"]["observe_timeout_seconds"] == 480
    rejected = client.put("/api/settings", json={"multimodal": {"provider": "intranet_oss"}})
    assert rejected.status_code == 400
    # restore default so other tests stay local
    client.put(
        "/api/settings",
        json={
            "multimodal": {"provider": "local_ffmpeg", "base_url": ""},
            "understand": {
                "observe": True,
                "clip_workers": 3,
                "gemini_max_size_mb": 15,
                "observe_timeout_seconds": 360,
            },
        },
    )


def test_cards_include_tags_and_omit_blank_observe():
    public = {
        "id": "prd_x",
        "status": "running",
        "source_video": {"upload_id": "up1"},
        "decomposition": {
            "summary": "本地摘要",
            "description": "描述",
            "provider": "local_ffmpeg",
            "video_tags": {"product_name": ["虾"], "video_describe": "描述", "style_categories": []},
            "segments": [
                {
                    "id": "seg_01",
                    "role": "hook",
                    "start_ms": 0,
                    "end_ms": 2700,
                    "summary": "开场",
                    "slot": ["开场钩子"],
                    "sub_slot": ["结果钩子"],
                    "clip_url": "/api/productions/prd_x/artifacts/clips/seg_01.mp4",
                }
            ],
        },
    }
    understanding = understanding_payload(public)
    assert understanding["description"] == "描述"
    assert understanding["video_tags"]["product_name"] == ["虾"]
    assert observe_payload(public) is None
    types = [item_type for item_type, _payload in cards_for(public)]
    assert "video_understanding" in types
    assert "video_observe" not in types
    public["decomposition"]["segments"][0]["observe"] = {
        "shot_observation_card": [{"visual_subjects": "虾"}],
        "raw_claims": [],
        "speech_evidence": {"status": "not_used", "segments": []},
    }
    assert observe_payload(public)["segments"][0]["id"] == "seg_01"
    assert observe_payload(public)["status"] == "running"
    assert "updated_at" in observe_payload(public)
    assert "video_observe" in [item_type for item_type, _ in cards_for(public)]


def test_remote_failure_falls_back_and_pipeline_still_completes(client: TestClient, monkeypatch):
    from video_fission.understand import service as understand_service

    def boom(*_args, **_kwargs):
        raise RuntimeError("fake gateway down")

    monkeypatch.setattr(understand_service, "_remote_split", boom)
    settings = client.put(
        "/api/settings",
        json={
            "multimodal": {
                "provider": "openai_compatible",
                "base_url": "https://example.invalid/v1",
                "api_key": "sk-test",
            }
        },
    )
    assert settings.status_code == 200, settings.text
    with FIXTURE.open("rb") as handle:
        uploaded = client.post(
            "/api/productions/uploads", files={"file": ("sample-reference.mp4", handle, "video/mp4")}
        )
    assert uploaded.status_code == 201, uploaded.text
    created = client.post(
        "/api/productions",
        json={"title": "fallback", "brief": "做一条短视频", "video_upload_id": uploaded.json()["id"]},
    )
    assert created.status_code == 201, created.text
    production_id = created.json()["id"]
    from video_fission import container

    finished = finish_production(production_id, container.productions.engine.run(production_id))
    public = container.productions.get(production_id)
    client.put("/api/settings", json={"multimodal": {"provider": "local_ffmpeg", "base_url": "", "api_key": None}})
    assert finished["status"] == "completed"
    assert public["decomposition"]["provider"] == "local_ffmpeg"
    assert any("整片理解失败" in item or "已保留本地切分" in item for item in public["warnings"])
    assert any("fake gateway down" in item for item in public["warnings"])
    assert Path(public["output"]["video_path"]).is_file()
    clip = Path(container.settings.artifacts_dir) / "productions" / production_id / "clips" / "seg_01.mp4"
    assert clip.is_file()
    media = client.get(f"/api/productions/{production_id}/artifacts/clips/seg_01.mp4")
    assert media.status_code == 200
    assert media.headers["content-type"].startswith("video/")


def test_remote_split_repairs_partial_timeline_and_keeps_model_summary(monkeypatch, tmp_path: Path):
    from video_fission.understand import service as understand_service

    monkeypatch.setattr(
        understand_service,
        "ensure_under_size",
        lambda src, _tmp, _limit: (src, ""),
    )
    monkeypatch.setattr(understand_service.media, "extract_frames", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        understand_service,
        "_call_understand",
        lambda *_args, **_kwargs: (
            {
                "video_summary": "模型总结仍然可用",
                "video_lens": [
                    {"start_time": 0, "end_time": 3, "description": "开场"},
                    {"start_time": 5, "end_time": 8, "description": "结尾"},
                ],
            },
            False,
        ),
    )

    lenses, tags, _provider, _model, warning, _opening = understand_service._remote_split(
        FIXTURE,
        tmp_path,
        8.1,
        {
            "base_url": "https://gateway.example/chat",
            "model": "gemini-test",
        },
        "token",
        "gemini_gateway",
        {"gemini_max_size_mb": 15},
        TransitionInfo(),
        None,
    )

    assert len(lenses) == 2
    assert coverage_ok_lenses(lenses, 8.1)
    assert tags["video_summary"] == "模型总结仍然可用"
    assert "时间轴存在缺口或重叠" in warning


def test_remote_split_preserves_tags_when_model_returns_no_lenses(monkeypatch, tmp_path: Path):
    from video_fission.understand import service as understand_service

    monkeypatch.setattr(
        understand_service,
        "ensure_under_size",
        lambda src, _tmp, _limit: (src, ""),
    )
    monkeypatch.setattr(understand_service.media, "extract_frames", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        understand_service,
        "_call_understand",
        lambda *_args, **_kwargs: ({"video_summary": "只有摘要"}, False),
    )

    lenses, tags, _provider, _model, warning, _opening = understand_service._remote_split(
        FIXTURE,
        tmp_path,
        8.1,
        {
            "base_url": "https://gateway.example/chat",
            "model": "gemini-test",
        },
        "token",
        "gemini_gateway",
        {"gemini_max_size_mb": 15},
        TransitionInfo(),
        None,
    )

    assert lenses == []
    assert tags["video_summary"] == "只有摘要"
    assert "已使用本地切分" in warning


def test_remote_split_uses_gemini_timeout_and_selects_best_candidate(
    monkeypatch,
    tmp_path: Path,
):
    from video_fission.understand import service as understand_service

    monkeypatch.setattr(
        understand_service,
        "ensure_under_size",
        lambda src, _tmp, _limit: (src, ""),
    )
    monkeypatch.setattr(understand_service.media, "extract_frames", lambda *_args, **_kwargs: [])
    calls: list[dict] = []
    payloads = [
        {
            "video_lens": [{"start_time": 0, "end_time": 8.1, "description": "候选一"}],
            "video_summary": "",
        },
        {
            "video_lens": [
                {"start_time": 0, "end_time": 4.0, "description": "候选二 A"},
                {"start_time": 4.0, "end_time": 8.1, "description": "候选二 B"},
            ],
            "video_summary": "更完整的候选",
        },
    ]

    def fake_call(_provider, _mm, _api_key, _prompt, _video, _frames, **kwargs):
        calls.append(kwargs)
        return payloads[len(calls) - 1], False

    monkeypatch.setattr(understand_service, "_call_understand", fake_call)

    lenses, tags, _provider, _model, _warning, _opening = understand_service._remote_split(
        FIXTURE,
        tmp_path,
        8.1,
        {
            "base_url": "https://gateway.example/chat",
            "model": "gemini-test",
        },
        "token",
        "gemini_gateway",
        {"gemini_max_size_mb": 15},
        TransitionInfo(),
        None,
    )

    assert len(calls) == 2
    assert all(call["timeout_seconds"] == 120 for call in calls)
    assert len(lenses) == 2
    assert lenses[0]["description"] == "候选二 A"
    assert tags["video_summary"] == "更完整的候选"


def test_clip_analysis_uses_short_gemini_timeout(monkeypatch, tmp_path: Path):
    from video_fission.understand import service as understand_service

    captured: dict = {}

    def fake_call(_provider, _mm, _api_key, _prompt, _video, _frames, **kwargs):
        captured.update(kwargs)
        return {
            "is_include_product": "是",
            "clip_description": "片段描述",
        }, False

    def fake_run_workers(segments, work, *_args, **_kwargs):
        segment_id, payload, _error = work(segments[0])
        return {segment_id: payload}

    monkeypatch.setattr(understand_service, "_call_understand", fake_call)
    monkeypatch.setattr(understand_service, "_run_workers", fake_run_workers)
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
        },
        "token",
        "gemini_gateway",
        {"clip_workers": 1},
        {},
        [],
        lambda *_args: None,
        None,
    )

    assert captured["timeout_seconds"] == 120


def test_split_prompt_budget_uses_compact_then_frames():
    from video_fission.understand import service as understand_service

    prompt, mode, prompt_tokens, video_tokens, estimated = understand_service._select_split_prompt(100.067)
    assert mode == "compact_video"
    assert prompt == build_compact_split_prompt(100.067)
    assert prompt != build_split_prompt(100.067)
    assert prompt_tokens + video_tokens == estimated
    assert estimated <= understand_service.VIDEO_INPUT_TOKEN_BUDGET

    short_prompt, short_mode, *_ = understand_service._select_split_prompt(8.1)
    assert short_mode == "full_video"
    assert short_prompt == build_split_prompt(8.1)

    long_prompt, long_mode, *_ = understand_service._select_split_prompt(300)
    assert long_mode == "frames"
    assert long_prompt == build_split_prompt(300)


def test_compact_split_prompt_keeps_output_contract():
    prompt = build_compact_split_prompt(100.067)
    for required in (
        "video_lens",
        "opening",
        "tag_level1",
        "tag_level2",
        "tag_level3",
        "product_name",
        "environment_categories",
        "BGM_styles",
        "video_describe",
        "video_summary",
    ):
        assert required in prompt
    assert "100.1 秒" in prompt


def test_remote_split_uses_compact_prompt_for_long_video(monkeypatch, tmp_path: Path):
    from video_fission.understand import service as understand_service

    monkeypatch.setattr(understand_service, "ensure_under_size", lambda src, _tmp, _limit: (src, ""))
    monkeypatch.setattr(understand_service.media, "extract_frames", lambda *_args, **_kwargs: [])
    captured: dict = {}

    def fake_call(_provider, _mm, _api_key, prompt, video_path, frames, temperature, **kwargs):
        captured.update(
            {
                "prompt": prompt,
                "video_path": video_path,
                "frames": frames,
                "temperature": temperature,
            }
        )
        return (
            {
                "video_summary": "模型总结",
                "video_lens": [{"start_time": 0, "end_time": 100.067, "description": "完整片段"}],
            },
            False,
        )

    monkeypatch.setattr(understand_service, "_call_understand", fake_call)
    lenses, _tags, _provider, _model, warning, _opening = understand_service._remote_split(
        FIXTURE,
        tmp_path,
        100.067,
        {
            "base_url": "https://gateway.example/chat",
            "model": "gemini-test",
        },
        "token",
        "gemini_gateway",
        {"gemini_max_size_mb": 15},
        TransitionInfo(),
        None,
    )

    assert len(lenses) == 1
    assert captured["prompt"] == build_compact_split_prompt(100.067)
    assert captured["video_path"] == FIXTURE
    assert "已使用精简提示词" in warning


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("torch") is None
    or __import__("importlib").util.find_spec("cv2") is None
    or __import__("importlib").util.find_spec("scenedetect") is None,
    reason="shot-detect extra 未安装",
)
def test_transition_detector_runs_when_extra_available():
    info = TransitionDetector().analyze(FIXTURE)
    assert info.ok is True
    assert info.fps > 0


def test_rerun_removes_stale_observe_card(client: TestClient):
    from video_fission import container

    conversation = container.hub.create_conversation({"title": "observe stale"})
    production = container.db.create_production(
        {
            "id": "prd_staleobserve0000000000",
            "title": "stale",
            "brief": "brief",
            "status": "running",
            "current_stage": "decompose",
            "warnings": [],
            "source_video": {},
            "clips": [],
            "video_upload_id": "upl_stale",
            "conversation_id": conversation["id"],
        }
    )
    decomposition = {
        "summary": "summary",
        "segments": [
            {
                "id": "seg_01",
                "role": "hook",
                "start_ms": 0,
                "end_ms": 2000,
                "summary": "hook",
                "observe": {"shot_observation_card": [{"visual_subjects": "item"}]},
            }
        ],
    }
    container.db.update_production(production["id"], {"decomposition": decomposition})
    container.productions.sync_conversation_cards(production["id"])
    observe_item_id = stable_item_id(production["id"], "video_observe")
    assert container.db.get_item(observe_item_id) is not None

    decomposition["segments"][0].pop("observe")
    container.db.update_production(production["id"], {"decomposition": decomposition})
    container.productions.sync_conversation_cards(production["id"])
    assert container.db.get_item(observe_item_id) is None
