from __future__ import annotations

import json
import threading

from tests.helpers import (
    called_tools,
    create_production,
    inject_fake,
    plan_until_scripts,
    plan_with_premature_generate,
    stub_candidates,
)
from video_fission.production_agent import CODEX_UNAVAILABLE, FakeCodexRuntime, _prompt_context


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
    assert result["status"] == "awaiting_script_choice"
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


def test_start_and_run_tools_hand_off_to_agent(app_env):
    from tests.helpers import upload_fixture

    inject_fake(app_env, plan_until_scripts())
    engine_called = []
    app_env.productions.engine.run = lambda *a, **k: engine_called.append(1)
    real_run = app_env.productions.run

    def wait_run(production_id, wait=False, **kwargs):
        return real_run(production_id, wait=True, **kwargs)

    app_env.productions.run = wait_run
    upload = upload_fixture(app_env.productions)
    started = app_env.tools.execute(
        "start_video_production",
        {"brief": "做一个开场抓人的产品片，全部新生成", "video_upload_id": upload["id"]},
        {},
    )
    assert started["success"]
    payload = json.loads(started["contentItems"][0]["text"])
    assert payload["production"]["status"] == "awaiting_script_choice"
    assert "Production Agent" in payload["message"]
    second = create_production(app_env.productions)
    inject_fake(app_env, plan_until_scripts())
    resumed = app_env.tools.execute(
        "run_video_production",
        {"production_id": second["id"]},
        {"production_id": second["id"]},
    )
    assert resumed["success"]
    resume_payload = json.loads(resumed["contentItems"][0]["text"])
    assert resume_payload["production"]["status"] == "awaiting_script_choice"
    assert "Production Agent" in resume_payload["message"]
    assert engine_called == []


def test_select_script_resumes_via_agent_and_refuses_generate_without_key(app_env):
    production = create_production(app_env.productions)
    inject_fake(app_env, plan_until_scripts())
    paused = app_env.productions.run(production["id"], wait=True)
    assert paused["status"] == "awaiting_script_choice"
    inject_fake(app_env, {"skills": ["production-orchestration"], "steps": [{"tool": "generate_video_shots"}]})
    result = app_env.productions.select_script(production["id"], "script_1", wait=True)
    assert result["id"] == production["id"]
    assert result["status"] == "failed"
    error = result.get("error") or ""
    assert "Key" in error or "base_url" in error or "无法出片" in error
    for phrase in ("已还原参考", "已按参考对齐", "已还原"):
        assert phrase not in error
    assert "generate_video_shots" in called_tools(result)


def test_agent_pauses_before_generate_if_script_not_selected(app_env):
    production = create_production(app_env.productions)
    inject_fake(app_env, plan_with_premature_generate())
    result = app_env.productions.run(production["id"], wait=True)
    assert result["status"] == "awaiting_script_choice"
    assert "generate_video_shots" not in called_tools(result)
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
    assert current["status"] == "awaiting_script_choice"


def test_fake_runtime_never_needs_codex_binary(app_env):
    assert app_env.settings.codex_bin in {"", None}
    runtime = FakeCodexRuntime(plan_until_scripts())
    plan = runtime.plan({"production_id": "prd_test"})
    assert plan["steps"]
    assert runtime.is_available()


def test_prompt_context_excludes_cancel_event():
    context = {
        "production_id": "prd_test",
        "cancel": threading.Event(),
        "production": {"id": "prd_test", "brief": "测试出片"},
    }

    prompt_context = _prompt_context(context)

    assert "cancel" not in prompt_context
    assert json.loads(json.dumps(prompt_context, ensure_ascii=False)) == prompt_context
