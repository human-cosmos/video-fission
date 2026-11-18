from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from video_fission.container import configure
from video_fission.conversation_cards import observe_payload, plan_payload, segments_payload, understanding_payload
from video_fission.main import app
from video_fission.pipeline import local_script, merge_opening_script, plan_segments, shot_edge_fades
from video_fission.replace import apply_replace_plan, resolve_replace_plan
from video_fission.understand.opening import estimate_local_opening, parse_opening, snap_opening_end
from video_fission.understand.prompts import prompt_video_split

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample-reference.mp4"


@pytest.fixture
def client(test_settings):
    configure(test_settings)
    with TestClient(app) as test_client:
        yield test_client


def test_prompt_requires_opening_and_distinguishes_hook():
    assert "视频开头区间" in prompt_video_split
    assert '"opening"' in prompt_video_split or '"opening":' in prompt_video_split
    assert "开头不是 hook" in prompt_video_split or "开头不是 hook" in prompt_video_split.replace("hook", "hook")
    assert "不得把钩子时长与开头区间画等号" in prompt_video_split
    assert "第一个片段通常是钩子，不超过 10 秒" not in prompt_video_split
    assert "reason" in prompt_video_split


def test_parse_opening_reads_model_json_and_rejects_invalid():
    parsed = parse_opening(
        {
            "opening": {
                "start_time": "00:00",
                "end_time": "00:12.4",
                "reason": "旁白说完处境后画面切到卖点演示，开场节奏在此收住。",
            }
        },
        20000,
    )
    assert parsed is not None
    assert parsed["start_ms"] == 0
    assert parsed["end_ms"] == 12400
    assert parsed["method"] == "model"
    assert "卖点" in parsed["reason"]
    assert parse_opening({}, 20000) is None
    assert parse_opening({"opening": {"reason": "约 12 秒"}}, 20000) is None
    assert parse_opening({"opening": {"end_time": "00:00", "reason": "空"}}, 20000) is None
    # 缺字段不得静默用 10–15 秒冒充模型结果
    missing = parse_opening({"opening": {"start_time": "00:00"}}, 20000)
    assert missing is None


def test_snap_opening_end_only_uses_window():
    assert snap_opening_end(12400, duration_ms=30000, cuts_ms=[12600, 15000], silence_ms=[]) == 12600
    assert snap_opening_end(12400, duration_ms=30000, cuts_ms=[15000], silence_ms=[]) == 12400


def test_snap_prefers_sentence_end_inside_window():
    # 切点 12.6s 与句末 12.2s 都在 ±600ms 内，应选句末，避免切断口播。
    assert snap_opening_end(12400, duration_ms=30000, cuts_ms=[12600], silence_ms=[12200]) == 12200


def test_short_film_model_opening_covers_duration():
    parsed = parse_opening({"opening": {"end_time": "00:08.1", "reason": "全片都是同一开场节奏。"}}, 8100)
    assert parsed is not None
    end = snap_opening_end(parsed["end_ms"], duration_ms=8100, cuts_ms=[2700, 5400], silence_ms=[])
    assert end == 8100


def test_local_estimate_is_fallback_not_model():
    opening = estimate_local_opening(8100, [2700, 5400], [])
    assert opening["method"] == "local_fallback"
    assert opening["end_ms"] == 8100
    assert opening["start_ms"] == 0


def test_plan_segments_does_not_invent_first_cut():
    segments = plan_segments(20000, [], [], False)
    assert segments[0]["end_ms"] > 3000
    assert (segments[0]["end_ms"] not in {1500, 2000, 3000})
    # 有真实切点时仍吸到 2700
    with_cuts = plan_segments(8100, [2700, 5400], [], False)
    assert with_cuts[0]["end_ms"] == 2700


def _production_with_opening(method: str = "model", end_ms: int = 12400) -> dict:
    return {
        "brief": "用香酥虾做一条短视频",
        "source_video": {"width": 1920, "height": 1080, "duration_ms": 20000},
        "skeleton": {"hook": "先抓住注意", "value": "展示卖点", "cta": "行动号召"},
        "decomposition": {
            "opening": {
                "start_ms": 0,
                "end_ms": end_ms,
                "reason": "铺完处境后切到产品特写。",
                "method": method,
                "segment_ids": ["seg_01", "seg_02"],
            },
            "segments": [
                {"id": "seg_01", "role": "hook", "start_ms": 0, "end_ms": 2700, "summary": "开场钩子", "visual": "hook"},
                {"id": "seg_02", "role": "value", "start_ms": 2700, "end_ms": 12400, "summary": "铺处境", "visual": "value"},
                {"id": "seg_03", "role": "value", "start_ms": 12400, "end_ms": 17000, "summary": "卖点展示", "visual": "value2"},
                {"id": "seg_04", "role": "cta", "start_ms": 17000, "end_ms": 20000, "summary": "行动号召", "visual": "cta"},
            ],
        },
    }


def test_local_script_merges_model_opening_into_one_shot():
    script = local_script(_production_with_opening())
    opening_shots = [shot for shot in script["shots"] if shot.get("id") == "shot_opening"]
    assert len(opening_shots) == 1
    assert opening_shots[0]["duration_ms"] == 12400
    assert opening_shots[0]["action"] == "reuse"
    assert opening_shots[0]["source_kind"] == "opening"
    assert script["opening"]["shot_id"] == "shot_opening"
    assert script["opening"]["end_ms"] == 12400
    # 开头之后仍保留后续镜头
    assert [shot["source_segment_id"] for shot in script["shots"][1:]] == ["seg_03", "seg_04"]
    assert script["total_duration_ms"] == 20000


def test_replace_segment_inside_opening_marks_whole_block_generate():
    production = _production_with_opening()
    plan = resolve_replace_plan(
        {"mode": "selected", "segment_ids": ["seg_02"]},
        production["decomposition"]["segments"],
        "",
        opening=production["decomposition"]["opening"],
    )
    assert "seg_01" in plan["resolved_segment_ids"]
    assert "seg_02" in plan["resolved_segment_ids"]
    production["replace_plan"] = plan
    script = local_script(production)
    script = apply_replace_plan(script, production)
    opening = next(shot for shot in script["shots"] if shot["id"] == "shot_opening")
    assert opening["action"] == "generate"
    assert opening["duration_ms"] == 12400
    later = [shot for shot in script["shots"] if shot["id"] != "shot_opening"]
    assert all(shot["action"] == "reuse" or shot.get("source_segment_id") != "seg_02" for shot in later)


def test_normalize_script_merges_legacy_split_shots():
    production = _production_with_opening()
    raw = {
        "shots": [
            {"id": "shot_01", "role": "hook", "duration_ms": 2700, "action": "generate", "source_segment_id": "seg_01", "visual": "a", "prompt": "a"},
            {"id": "shot_02", "role": "value", "duration_ms": 9700, "action": "reuse", "source_segment_id": "seg_02", "visual": "b"},
            {"id": "shot_03", "role": "value", "duration_ms": 4600, "action": "reuse", "source_segment_id": "seg_03", "visual": "c"},
            {"id": "shot_04", "role": "cta", "duration_ms": 3000, "action": "generate", "source_segment_id": "seg_04", "visual": "d", "prompt": "d"},
        ]
    }
    script = merge_opening_script({"shots": raw["shots"], "total_duration_ms": 20000, "audio_plan": {"voiceover_language": "zh", "bgm": "reuse_reference", "subtitles": True}}, production)
    assert script["shots"][0]["id"] == "shot_opening"
    assert script["shots"][0]["duration_ms"] == 12400
    assert script["shots"][0]["action"] == "reuse"
    assert [shot["id"] for shot in script["shots"][1:]] == ["shot_03", "shot_04"]


def test_opening_has_no_internal_fade_but_allows_boundary():
    shots = [
        {"id": "shot_opening", "action": "reuse", "source_kind": "opening", "duration_ms": 12400},
        {"id": "shot_02", "action": "generate", "duration_ms": 4000},
        {"id": "shot_03", "action": "reuse", "duration_ms": 3600},
    ]
    assert shot_edge_fades(0, shots, "shot_opening") == (False, True)
    assert shot_edge_fades(1, shots, "shot_opening") == (True, True)
    assert shot_edge_fades(2, shots, "shot_opening") == (True, False)
    # 单块开头：内部不得淡化
    only = [{"id": "shot_opening", "action": "reuse", "source_kind": "opening", "duration_ms": 8100}]
    assert shot_edge_fades(0, only, "shot_opening") == (False, False)


def test_cards_pass_opening_and_omit_blank_observe():
    public = {
        "id": "prd_x",
        "status": "running",
        "current_stage": "decompose",
        "updated_at": "now",
        "source_video": {},
        "decomposition": {
            "summary": "s",
            "description": "",
            "provider": "local_ffmpeg",
            "model": "",
            "video_tags": {},
            "segments": [{"id": "seg_01", "role": "hook", "start_ms": 0, "end_ms": 8100}],
            "opening": {
                "start_ms": 0,
                "end_ms": 8100,
                "reason": "短片本地暂估",
                "method": "local_fallback",
                "segment_ids": ["seg_01"],
            },
        },
    }
    public["source_video"] = {"duration_ms": 40000}
    public["script"] = {
        "shots": [{"id": "shot_opening", "duration_ms": 8100}],
        "opening": {"start_ms": 0, "end_ms": 8100, "shot_id": "shot_opening"},
    }
    understanding = understanding_payload(public)
    segments = segments_payload(public)
    plan = plan_payload(public)
    assert understanding["opening"]["method"] == "local_fallback"
    assert segments["opening"]["end_ms"] == 8100
    assert plan["opening"]["method"] == "local_fallback"
    assert plan["opening"]["end_ms"] == 8100
    assert plan["duration_ms"] == 40000
    assert "method" not in (plan["script"]["opening"] or {})
    assert observe_payload(public) is None


def test_no_key_pipeline_keeps_local_fallback_opening(client: TestClient, test_settings):
    uploaded = client.post(
        "/api/productions/uploads",
        files={"file": ("sample-reference.mp4", FIXTURE.read_bytes(), "video/mp4")},
    )
    assert uploaded.status_code == 201, uploaded.text
    created = client.post(
        "/api/productions",
        json={"brief": "用香酥虾做一条短视频", "video_upload_id": uploaded.json()["id"]},
    )
    assert created.status_code == 201, created.text
    production_id = created.json()["id"]
    from video_fission import container

    public = container.productions.run(production_id, wait=True)
    assert public["status"] == "completed", public.get("error")
    opening = (public.get("decomposition") or {}).get("opening") or {}
    assert opening.get("method") == "local_fallback"
    assert opening.get("start_ms") == 0
    assert int(opening.get("end_ms") or 0) == int(public["source_video"]["duration_ms"])
    script = public.get("script") or {}
    opening_shots = [shot for shot in script.get("shots") or [] if shot.get("id") == "shot_opening"]
    assert len(opening_shots) == 1
    assert opening_shots[0]["duration_ms"] == opening["end_ms"]
    assert opening_shots[0]["action"] == "reuse"
    clip = Path(test_settings.artifacts_dir) / "productions" / production_id / "clips" / "opening.mp4"
    assert clip.is_file()
    media = client.get(f"/api/productions/{production_id}/artifacts/clips/opening.mp4")
    assert media.status_code == 200
    # 无 Key 不插空白观察卡
    assert observe_payload(public) is None
