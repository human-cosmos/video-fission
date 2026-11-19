from __future__ import annotations

from pathlib import Path

import pytest
from video_fission.pipeline import PipelineEngine
from video_fission.production_tools import (
    CAPABILITY_TOOLS,
    FORBIDDEN_CLAIMS,
    ProductionToolError,
    contains_forbidden_claim,
)

from tests.helpers import create_production, seed_clips_from_fixture, stub_candidates


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
        "generate_candidate_videos",
        "generate_video_shots",
        "separate_audio",
        "burn_subtitles",
        "compose_timeline",
        "handle_production_followup",
        "prepare_video_production",
        "start_video_production",
        "run_video_production",
    }
    assert expected <= names
    model_visible = {spec["name"] for spec in app_env.tools.specs()}
    assert "prepare_video_production" in model_visible
    assert "run_video_production" in model_visible
    assert "start_video_production" not in model_visible
    assert not (set(CAPABILITY_TOOLS) & model_visible)


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


def test_write_intent_requires_exactly_three_candidates_and_continues(app_env):
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
    assert written["status"] == "running"
    assert written["paused"] is False
    production = app_env.db.get_production(pid)
    assert production["status"] == "running"
    assert production.get("selected_script_id") is None
    assert len(production["script_candidates"]) == 3
    assert production.get("candidate_videos") == []


def test_candidate_generation_fails_honestly_without_key(app_env):
    production = create_production(app_env.productions)
    pid = production["id"]
    _payload(_execute(app_env, "ingest_reference_video", pid))
    _payload(_execute(app_env, "understand_reference_video", pid))
    _payload(_execute(app_env, "lock_timeline_opening", pid))
    _payload(_execute(app_env, "write_intent_and_scripts", pid, script_candidates=stub_candidates()))
    text = _fail(app_env, "generate_candidate_videos", pid)
    assert "Key" in text or "base_url" in text or "无法出片" in text
    for phrase in FORBIDDEN_CLAIMS:
        assert phrase not in text
    production = app_env.db.get_production(pid)
    output_blob = str(production.get("output") or "") + str(production.get("error") or "")
    for phrase in FORBIDDEN_CLAIMS:
        assert phrase not in output_blob


def test_get_state_does_not_rewrite_creative_context(app_env, monkeypatch):
    production = create_production(app_env.productions)
    pid = production["id"]
    _payload(_execute(app_env, "ingest_reference_video", pid))
    _payload(_execute(app_env, "understand_reference_video", pid))

    def forbidden_write(*_args, **_kwargs):
        raise AssertionError("get_production_state 不应写入上下文文件")

    monkeypatch.setattr("video_fission.production_tools.write_creative_context", forbidden_write)
    state = _payload(_execute(app_env, "get_production_state", pid))
    assert state["artifacts"]["context_path"].endswith("creative-context.json")


def test_candidate_generation_outputs_three_videos_and_pauses(app_env, monkeypatch):
    production = create_production(app_env.productions)
    pid = production["id"]
    _payload(_execute(app_env, "ingest_reference_video", pid))
    _payload(_execute(app_env, "understand_reference_video", pid))
    _payload(_execute(app_env, "lock_timeline_opening", pid))
    _payload(_execute(app_env, "write_intent_and_scripts", pid, script_candidates=stub_candidates()))
    host = app_env.productions.tool_host
    artifact_dir = app_env.productions.engine.artifact_dir(pid)

    monkeypatch.setattr(
        host,
        "separate_audio",
        lambda *args, **kwargs: {"production_id": pid, "usable": False},
    )
    monkeypatch.setattr(
        host,
        "_validate_video_generation",
        lambda *args, **kwargs: ({}, {}, "fixture"),
    )

    def fake_generate(arguments, context):
        current = app_env.db.get_production(pid)
        clips = [
            {
                "shot_id": shot["id"],
                "path": str(artifact_dir / f"{current['selected_script_id']}-{shot['id']}.mp4"),
                "provider": "fixture",
                "duration_ms": shot.get("duration_ms") or 1000,
            }
            for shot in (current.get("script") or {}).get("shots") or []
        ]
        app_env.db.update_production(pid, {"clips": clips})
        return {"production_id": pid, "clips": clips}

    def fake_compose(arguments, context):
        current = app_env.db.get_production(pid)
        candidate_id = current["selected_script_id"]
        video_path = artifact_dir / f"candidate-{candidate_id}.mp4"
        video_path.write_bytes(b"candidate-video")
        output = {
            "video_path": str(video_path),
            "composed_video_path": str(video_path),
            "video_url": f"/api/productions/{pid}/artifacts/{video_path.name}",
        }
        app_env.db.update_production(pid, {"output": output})
        return {
            "production_id": pid,
            "video_path": str(video_path),
            "video_url": output["video_url"],
            "output": output,
        }

    monkeypatch.setattr(host, "generate_video_shots", fake_generate)
    monkeypatch.setattr(host, "compose_timeline", fake_compose)

    result = host.generate_candidate_videos({"production_id": pid}, {"production_id": pid})

    assert result["status"] == "awaiting_video_choice"
    current = app_env.db.get_production(pid)
    assert current["status"] == "awaiting_video_choice"
    assert current["selected_script_id"] is None
    assert not current["output"]
    assert len(current["candidate_videos"]) == 3
    assert [item["id"] for item in current["candidate_videos"]] == [
        "script_1",
        "script_2",
        "script_3",
    ]


def test_select_video_adopts_candidate_and_starts_finalize(app_env, monkeypatch):
    production = create_production(app_env.productions)
    pid = production["id"]
    candidates = stub_candidates()
    artifact_dir = app_env.productions.engine.artifact_dir(pid)
    candidate_videos = []
    for candidate in candidates:
        video_path = artifact_dir / f"candidate-{candidate['id']}.mp4"
        video_path.write_bytes(b"candidate")
        candidate_videos.append(
            {
                **candidate,
                "clips": [{"shot_id": "shot_opening", "path": str(video_path)}],
                "output": {
                    "video_path": str(video_path),
                    "video_url": f"/api/productions/{pid}/artifacts/{video_path.name}",
                },
            }
        )
    app_env.db.update_production(
        pid,
        {
            "script_candidates": candidates,
            "candidate_videos": candidate_videos,
            "status": "awaiting_video_choice",
            "current_stage": "compose",
        },
    )
    captured = {}

    def fake_run(production_id, wait=False, replace_policy=None, resume_from=None, user_id=None):
        captured["resume_from"] = resume_from
        return app_env.db.get_production(production_id)

    monkeypatch.setattr(app_env.productions, "run", fake_run)
    result = app_env.productions.select_video(pid, "script_2")

    assert result["selected_script_id"] == "script_2"
    assert captured["resume_from"] == "finalize"
    current = app_env.db.get_production(pid)
    assert current["selected_script_id"] == "script_2"
    assert current["script"] == candidates[1]["script"]
    assert current["output"]["video_path"].endswith("candidate-script_2.mp4")


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
    assert production["status"] == "running"
    selected = _payload(_execute(app_env, "handle_production_followup", pid, text="选方案 1"))
    assert selected["production_id"] == pid
    assert selected["action"] == "resume"
    assert selected["resume_from"] == "script"
    assert selected.get("paused") is not True
    production = app_env.db.get_production(pid)
    assert production["selected_script_id"] is None
    assert production["status"] == "running"


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
    assert composed["status"] == "running"
    assert app_env.db.get_production(pid)["status"] == "running"
    assert composed.get("video_path")
    production = app_env.db.get_production(pid)
    assert production["audio_separation"]["applied_to_output"] is True
    assert Path(production["audio_separation"]["bgm_path"]).is_file()
    burned = _payload(_execute(app_env, "burn_subtitles", pid))
    assert burned.get("srt_path")
    assert burned.get("video_path")
    if not burned.get("aligned"):
        assert "未按参考对齐" in burned.get("note", "")
    for phrase in FORBIDDEN_CLAIMS:
        assert phrase not in str(burned)
    state = _payload(_execute(app_env, "get_production_state", pid))
    assert state["artifacts"]["video_path"]
    progressed = _payload(
        _execute(app_env, "update_production_progress", pid, message="合成完成", current_stage="compose")
    )
    assert progressed["current_stage"] == "compose"
    assert not _execute(app_env, "update_production_progress", pid, status="completed")["success"]


def test_pipeline_engine_run_is_no_longer_an_entry(app_env):
    with pytest.raises(RuntimeError, match="确定性执行器"):
        app_env.productions.engine.run("prd_unused")


def test_forbidden_claim_helper():
    assert contains_forbidden_claim("画面已还原参考")
    assert not contains_forbidden_claim("字幕使用安全默认位置，未按参考对齐。")


def test_production_tool_error_type():
    assert issubclass(ProductionToolError, ValueError)
    assert issubclass(PipelineEngine, object)
