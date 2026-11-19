from __future__ import annotations

import pytest

from tests.helpers import create_production, seed_clips_from_fixture, stub_candidates
from video_fission.pipeline import PipelineEngine
from video_fission.production_tools import FORBIDDEN_CLAIMS, ProductionToolError, contains_forbidden_claim


def _execute(app_env, name: str, production_id: str, **arguments):
    arguments = {"production_id": production_id, **arguments}
    return app_env.tools.execute(name, arguments, {"production_id": production_id})


def _payload(result: dict) -> dict:
    assert result["success"], result
    import json

    return json.loads(result["contentItems"][0]["text"])


def _fail(app_env, name: str, production_id: str, **arguments) -> str:
    result = _execute(app_env, name, production_id, **arguments)
    assert not result["success"]
    return result["contentItems"][0]["text"]


def test_each_capability_tool_is_registered(app_env):
    names = {tool.name for tool in app_env.tools.all()}
    expected = {
        "ingest_reference_video",
        "understand_reference_video",
        "extract_landing_points",
        "search_video_references",
        "lock_timeline_opening",
        "write_intent_and_scripts",
        "get_production_state",
        "update_production_progress",
        "generate_video_shots",
        "separate_audio",
        "burn_subtitles",
        "compose_timeline",
        "handle_production_followup",
        "start_video_production",
        "run_video_production",
    }
    assert expected <= names


def test_opening_is_locked_as_one_continuous_block(app_env):
    production = create_production(app_env.productions)
    pid = production["id"]
    _payload(_execute(app_env, "ingest_reference_video", pid))
    understood = _payload(_execute(app_env, "understand_reference_video", pid))
    assert understood.get("degraded") is True or understood.get("used_understanding_model") is False
    for phrase in FORBIDDEN_CLAIMS:
        assert phrase not in str(understood)
    locked = _payload(_execute(app_env, "lock_timeline_opening", pid))
    opening = locked["opening"]
    end_ms = int(opening.get("end_ms") or 0)
    assert end_ms > 0
    opening_shots = [shot for shot in locked["shots"] if shot["id"] == "shot_opening"]
    assert len(opening_shots) == 1
    assert opening_shots[0]["duration_ms"] == end_ms
    production = app_env.db.get_production(pid)
    cursor = 0
    for shot in (production.get("script") or {}).get("shots") or []:
        start = int(shot.get("start_ms") or cursor)
        if shot.get("id") != "shot_opening":
            assert start >= end_ms
        cursor = start + int(shot.get("duration_ms") or 0)


def test_write_intent_requires_exactly_three_candidates_and_pauses(app_env):
    production = create_production(app_env.productions)
    pid = production["id"]
    _payload(_execute(app_env, "ingest_reference_video", pid))
    _payload(_execute(app_env, "understand_reference_video", pid))
    _payload(_execute(app_env, "lock_timeline_opening", pid))
    text = _fail(app_env, "write_intent_and_scripts", pid, script_candidates=stub_candidates()[:2])
    assert "3" in text or "三" in text
    written = _payload(
        _execute(
            app_env,
            "write_intent_and_scripts",
            pid,
            intent={"goal": "测"},
            skeleton={"one_liner": "测"},
            script_candidates=stub_candidates(),
        )
    )
    assert written["status"] == "awaiting_script_choice"
    assert written["paused"] is True
    production = app_env.db.get_production(pid)
    assert production["status"] == "awaiting_script_choice"
    assert production.get("selected_script_id") is None
    assert len(production["script_candidates"]) == 3


def test_generate_requires_selected_script_and_fails_honestly_without_key(app_env):
    production = create_production(app_env.productions)
    pid = production["id"]
    _payload(_execute(app_env, "ingest_reference_video", pid))
    _payload(_execute(app_env, "understand_reference_video", pid))
    _payload(_execute(app_env, "lock_timeline_opening", pid))
    _payload(_execute(app_env, "write_intent_and_scripts", pid, script_candidates=stub_candidates()))
    text = _fail(app_env, "generate_video_shots", pid)
    assert "选定" in text or "选择" in text
    chosen = stub_candidates()[0]["id"]
    # lock_script already stored on candidates
    production = app_env.db.get_production(pid)
    selected = next(item for item in production["script_candidates"] if item["id"] == chosen)
    app_env.db.update_production(
        pid,
        {"script": selected["script"], "selected_script_id": chosen, "status": "running"},
    )
    text = _fail(app_env, "generate_video_shots", pid)
    assert "Key" in text or "base_url" in text or "无法出片" in text
    for phrase in FORBIDDEN_CLAIMS:
        assert phrase not in text
    production = app_env.db.get_production(pid)
    output_blob = str(production.get("output") or "") + str(production.get("error") or "")
    for phrase in FORBIDDEN_CLAIMS:
        assert phrase not in output_blob


def test_followup_stays_on_same_production_and_does_not_restart_ingest(app_env):
    production = create_production(app_env.productions)
    pid = production["id"]
    _payload(_execute(app_env, "ingest_reference_video", pid))
    _payload(_execute(app_env, "understand_reference_video", pid))
    _payload(_execute(app_env, "lock_timeline_opening", pid))
    _payload(_execute(app_env, "write_intent_and_scripts", pid, script_candidates=stub_candidates()))
    clarified = _payload(_execute(app_env, "handle_production_followup", pid, text="再改一下"))
    assert clarified["production_id"] == pid
    assert clarified["action"] == "clarify"
    assert "ingest" not in (clarified.get("resume_from") or "")
    production = app_env.db.get_production(pid)
    assert production["id"] == pid
    assert production["status"] == "awaiting_script_choice"
    selected = _payload(_execute(app_env, "handle_production_followup", pid, text="选方案 1"))
    assert selected["production_id"] == pid
    assert selected["action"] == "select"
    assert selected.get("paused") is True
    production = app_env.db.get_production(pid)
    assert production["selected_script_id"] == "script_1"
    assert production["status"] == "awaiting_script_choice"


def test_ffmpeg_separate_compose_and_burn_use_official_fixture(app_env):
    production = create_production(app_env.productions)
    pid = production["id"]
    _payload(_execute(app_env, "ingest_reference_video", pid))
    _payload(_execute(app_env, "understand_reference_video", pid))
    _payload(_execute(app_env, "extract_landing_points", pid))
    refs = _payload(_execute(app_env, "search_video_references", pid))
    assert refs["count"] >= 1
    _payload(_execute(app_env, "lock_timeline_opening", pid))
    _payload(_execute(app_env, "write_intent_and_scripts", pid, script_candidates=stub_candidates()))
    production = app_env.db.get_production(pid)
    chosen = production["script_candidates"][0]
    app_env.db.update_production(
        pid,
        {
            "script": chosen["script"],
            "selected_script_id": chosen["id"],
            "status": "running",
        },
    )
    separated = _payload(_execute(app_env, "separate_audio", pid))
    assert separated.get("vocals_path") or separated.get("bgm_path") or separated.get("method")
    for phrase in FORBIDDEN_CLAIMS:
        assert phrase not in str(separated)
    seed_clips_from_fixture(app_env, pid)
    composed = _payload(_execute(app_env, "compose_timeline", pid))
    assert composed["status"] == "completed"
    assert composed.get("video_path")
    burned = _payload(_execute(app_env, "burn_subtitles", pid))
    assert burned.get("srt_path")
    assert burned.get("video_path")
    if not burned.get("aligned"):
        assert "未按参考对齐" in burned.get("note", "")
    for phrase in FORBIDDEN_CLAIMS:
        assert phrase not in str(burned)
    state = _payload(_execute(app_env, "get_production_state", pid))
    assert state["artifacts"]["video_path"]
    progressed = _payload(_execute(app_env, "update_production_progress", pid, message="合成完成", current_stage="done"))
    assert progressed["current_stage"] == "done"


def test_pipeline_engine_run_is_no_longer_an_entry(app_env):
    with pytest.raises(RuntimeError, match="Production Agent"):
        app_env.productions.engine.run("prd_unused")


def test_forbidden_claim_helper():
    assert contains_forbidden_claim("画面已还原参考")
    assert not contains_forbidden_claim("字幕使用安全默认位置，未按参考对齐。")


def test_production_tool_error_type():
    assert issubclass(ProductionToolError, ValueError)
    assert issubclass(PipelineEngine, object)
