from __future__ import annotations

import json
import threading
from pathlib import Path

from video_fission.production_agent import (
    CODEX_UNAVAILABLE,
    DeterministicPlanner,
    FakeCodexRuntime,
)

from tests.helpers import (
    called_tools,
    create_production,
    inject_fake,
    plan_until_scripts,
    plan_with_premature_generate,
    stub_candidates,
)


def test_run_goes_through_production_agent_not_pipeline_loop(app_env):
    production = create_production(app_env.productions)
    runtime = inject_fake(app_env, plan_until_scripts())
    called = []

    def forbidden(*_args, **_kwargs):
        called.append(1)
        raise AssertionError("PipelineEngine.run 不应被调用")

    app_env.productions.engine.run = forbidden
    result = app_env.productions.run(production["id"], wait=True)
    assert called == []
    assert runtime.plans
    assert result["status"] == "awaiting_script_approval"
    assert result.get("selected_script_id") is None
    tools = called_tools(result)
    assert tools == [
        "ingest_reference_video",
        "understand_reference_video",
        "extract_landing_points",
        "search_video_references",
        "lock_timeline_opening",
        "write_intent_and_scripts",
    ]
    kinds = [item.get("type") for item in result.get("agent_trace") or []]
    assert "plan" in kinds
    assert "skill" in kinds
    skills = [item.get("name") for item in result["agent_trace"] if item.get("type") == "skill"]
    assert "production-orchestration" in skills
    events = {item["type"] for item in app_env.db.list_production_events(production["id"])}
    assert "agent_plan" in events
    assert "skill_loaded" in events
    assert "tool_started" in events
    assert "tool_completed" in events


def test_default_planner_waits_for_script_approval_before_candidate_videos(app_env):
    production = create_production(app_env.productions)
    app_env.productions.update_creative_context(
        production["id"],
        intent={"goal": "测试", "audience": "测试", "promise": "测试", "assumptions": []},
        skeleton={"one_liner": "测试", "hook": "钩子", "value": "价值", "cta": "行动"},
        script_candidates=stub_candidates(),
    )

    pending = app_env.productions.run(production["id"], wait=True)

    assert isinstance(app_env.productions.agent.runtime, DeterministicPlanner)
    assert pending["status"] == "awaiting_script_approval"
    assert "generate_candidate_videos" not in called_tools(pending)
    result = app_env.productions.run(
        production["id"],
        wait=True,
        resume_from="generate",
        user_approved=True,
    )
    assert result["status"] == "failed"
    assert "Key" in (result.get("error") or "") or "base_url" in (result.get("error") or "")
    assert called_tools(result) == [
        "ingest_reference_video",
        "understand_reference_video",
        "extract_landing_points",
        "search_video_references",
        "lock_timeline_opening",
        "write_intent_and_scripts",
        "generate_candidate_videos",
    ]
    assert result.get("selected_script_id") is None


def test_prepare_builds_creative_context_before_scripts(app_env):
    production = create_production(app_env.productions)

    prepared = app_env.productions.prepare(production["id"])

    assert prepared["status"] == "awaiting_creative_context"
    assert prepared["current_stage"] == "intent"
    assert called_tools(prepared) == [
        "ingest_reference_video",
        "understand_reference_video",
        "extract_landing_points",
        "search_video_references",
    ]
    assert prepared["decomposition"]["segments"]
    assert prepared["intent"]["references"]
    assert prepared["script_candidates"] == []
    context_path = Path(prepared["creative_context"]["context_path"])
    timeline_path = Path(prepared["creative_context"]["timeline_path"])
    assert context_path.is_file()
    assert timeline_path.is_file()
    context = json.loads(context_path.read_text(encoding="utf-8"))
    assert context["production_id"] == production["id"]
    assert len(context["segments"]) == len(prepared["decomposition"]["segments"])
    assert all(str(segment["id"]) in timeline_path.read_text(encoding="utf-8") for segment in context["segments"])


def test_missing_creative_content_fails_before_media_tools(app_env):
    production = create_production(app_env.productions)

    result = app_env.productions.run(production["id"], wait=True)

    assert result["status"] == "failed"
    assert "缺少创意内容" in result["error"]
    assert called_tools(result) == []


def test_video_message_uses_current_conversation_codex(monkeypatch, app_env):
    from tests.helpers import upload_fixture

    upload = upload_fixture(app_env.productions)
    conversation = app_env.hub.create_conversation({"title": "创作对话"})
    captured = {}
    finished = threading.Event()

    def fake_run_turn(conversation_id, text, mentions, generation, lock):
        captured.update({"conversation_id": conversation_id, "text": text})
        if lock.locked():
            lock.release()
        finished.set()

    monkeypatch.setattr(app_env.hub, "_codex_available", lambda: True)
    monkeypatch.setattr(app_env.hub, "_run_turn", fake_run_turn)

    result = app_env.hub.send_message(
        conversation["id"],
        {
            "text": "按这个参考视频做一条卖点清晰的短视频",
            "files": [{"kind": "production_video", "id": upload["id"], "name": upload["name"]}],
        },
    )

    assert finished.wait(2)
    assert result["status"] == "running"
    assert "production_id" not in result
    assert f"video_upload_id=`{upload['id']}`" in captured["text"]
    assert app_env.db.list_productions(conversation_id=conversation["id"]) == []


def test_start_and_run_tools_hand_off_to_agent(app_env):
    from tests.helpers import upload_fixture

    engine_called = []
    app_env.productions.engine.run = lambda *a, **k: engine_called.append(1)
    real_run = app_env.productions.run

    def wait_run(production_id, wait=False, **kwargs):
        return real_run(production_id, wait=True, **kwargs)

    app_env.productions.run = wait_run
    upload = upload_fixture(app_env.productions)
    prepared = app_env.tools.execute(
        "prepare_video_production",
        {
            "brief": "做一个开场抓人的产品片，全部新生成",
            "video_upload_id": upload["id"],
        },
        {},
    )
    assert prepared["success"]
    prepared_payload = json.loads(prepared["contentItems"][0]["text"])
    production_id = prepared_payload["production"]["id"]
    assert prepared_payload["production"]["status"] == "awaiting_creative_context"
    assert prepared_payload["creative_context"]["decomposition"]["segments"]
    assert prepared_payload["creative_context"]["references"]
    assert Path(prepared_payload["creative_context"]["context_path"]).is_file()
    assert Path(prepared_payload["creative_context"]["timeline_path"]).is_file()

    resumed = app_env.tools.execute(
        "run_video_production",
        {
            "production_id": production_id,
            "resume_from": "script",
            "intent": {"goal": "测试", "audience": "测试", "promise": "测试", "assumptions": []},
            "skeleton": {"one_liner": "测试", "hook": "钩子", "value": "价值", "cta": "行动"},
            "script_candidates": stub_candidates(),
        },
        {"production_id": production_id},
    )
    assert resumed["success"]
    resume_payload = json.loads(resumed["contentItems"][0]["text"])
    assert resume_payload["production"]["status"] == "awaiting_script_approval"
    assert "generate_candidate_videos" not in called_tools(resume_payload["production"])
    assert "后台创作任务" in resume_payload["message"]
    approved = app_env.productions.approve_scripts(production_id)
    assert approved["status"] == "failed"
    assert "Key" in (approved.get("error") or "") or "base_url" in (approved.get("error") or "")
    assert "generate_candidate_videos" in called_tools(approved)
    assert engine_called == []


def test_candidate_generation_does_not_require_script_selection(app_env):
    production = create_production(app_env.productions)
    app_env.productions.update_creative_context(
        production["id"],
        intent={"goal": "测试", "audience": "测试", "promise": "测试", "assumptions": []},
        skeleton={"one_liner": "测试", "hook": "钩子", "value": "价值", "cta": "行动"},
        script_candidates=stub_candidates(),
    )
    pending = app_env.productions.run(production["id"], wait=True)
    assert pending["status"] == "awaiting_script_approval"
    result = app_env.productions.run(
        production["id"],
        wait=True,
        resume_from="generate",
        user_approved=True,
    )
    assert result["id"] == production["id"]
    assert result["status"] == "failed"
    error = result.get("error") or ""
    assert "Key" in error or "base_url" in error or "无法出片" in error
    for phrase in ("已还原参考", "已按参考对齐", "已还原"):
        assert phrase not in error
    assert "generate_candidate_videos" in called_tools(result)
    assert result.get("selected_script_id") is None


def test_recovery_migrates_legacy_script_choice_to_all_candidate_generation(app_env, monkeypatch):
    production = create_production(app_env.productions)
    app_env.db.update_production(
        production["id"],
        {
            "status": "awaiting_script_choice",
            "current_stage": "script",
            "script_candidates": stub_candidates(),
        },
    )
    captured = {}

    class Runtime:
        def run(self, production_id, cancel, resume_from=None):
            captured.update(
                {
                    "production_id": production_id,
                    "resume_from": resume_from,
                    "cancel": cancel,
                }
            )

    monkeypatch.setattr(app_env.productions, "_orchestrator", lambda: Runtime())

    assert app_env.productions.recover_orphaned_runs(wait=True) == [production["id"]]
    assert captured["production_id"] == production["id"]
    assert captured["resume_from"] == "generate"
    current = app_env.db.get_production(production["id"])
    assert current["status"] == "running"
    assert current["current_stage"] == "generate"


def test_bulk_shot_generation_still_rejects_missing_selection(app_env):
    production = create_production(app_env.productions)
    inject_fake(app_env, plan_with_premature_generate())
    pending = app_env.productions.run(production["id"], wait=True)
    assert pending["status"] in {"awaiting_script_approval", "failed"}
    inject_fake(app_env, {"steps": [{"tool": "generate_video_shots"}]})
    result = app_env.productions.run(
        production["id"],
        wait=True,
        resume_from="generate",
        user_approved=True,
    )
    assert result["status"] == "failed"
    assert "选定" in (result.get("error") or "") or "选择" in (result.get("error") or "")
    assert result.get("selected_script_id") is None


def test_codex_unavailable_fails_clearly(app_env):
    production = create_production(app_env.productions)
    inject_fake(app_env, available=False)
    result = app_env.productions.run(production["id"], wait=True)
    assert result["status"] == "failed"
    assert "Codex 不可用" in (result.get("error") or "")
    assert result.get("error") == CODEX_UNAVAILABLE or "无法编排出片" in (result.get("error") or "")
    assert called_tools(result) == []
    engine_called = []
    app_env.productions.engine.run = lambda *a, **k: engine_called.append(1)
    inject_fake(app_env, available=False)
    other = create_production(app_env.productions)
    app_env.productions.run(other["id"], wait=True)
    assert engine_called == []


def test_followup_tool_keeps_same_production(app_env):
    production = create_production(app_env.productions)
    inject_fake(app_env, plan_until_scripts())
    paused = app_env.productions.run(production["id"], wait=True)
    result = app_env.tools.execute(
        "handle_production_followup",
        {"production_id": paused["id"], "text": "不满意，再改一下"},
        {"production_id": paused["id"]},
    )
    assert result["success"]
    payload = json.loads(result["contentItems"][0]["text"])
    assert payload["production_id"] == paused["id"]
    assert payload["action"] == "clarify"
    current = app_env.db.get_production(paused["id"])
    assert current["id"] == paused["id"]
    assert current["status"] == "awaiting_script_approval"


def test_fake_runtime_never_needs_codex_binary(app_env):
    assert app_env.settings.codex_bin in {"", None}
    runtime = FakeCodexRuntime(plan_until_scripts())
    plan = runtime.plan({"production_id": "prd_test"})
    assert plan["steps"]
    assert runtime.is_available()
