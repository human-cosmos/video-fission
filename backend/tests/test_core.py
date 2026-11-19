from __future__ import annotations

import os
import re
import time
from pathlib import Path
from unittest.mock import Mock

import pytest
from video_fission.db import Database
from video_fission.references import ReferenceLibrary
from video_fission.service import RUNTIME_IDLE_SECONDS, CodexHub, ConversationBusy
from video_fission.skills import SkillManager, SkillValidationError
from video_fission.tools import build_registry


def test_database_persists_conversation_items_and_events(test_settings):
    db = Database(test_settings.database_path)
    db.initialize()
    conversation = db.create_conversation(
        {
            "cwd": str(test_settings.workspace),
            "model": "deepseek-flash",
            "reasoning_effort": "low",
        }
    )
    item = db.add_item(
        conversation["id"],
        {
            "item_type": "userMessage",
            "role": "user",
            "status": "completed",
            "payload": {"text": "persist me"},
        },
    )
    event_id = db.append_event(conversation["id"], "item.local", item)

    reopened = Database(test_settings.database_path)
    assert reopened.get_conversation(conversation["id"])["title"] == "新对话"
    assert reopened.list_items(conversation["id"])[0]["payload"]["text"] == "persist me"
    assert reopened.list_events(conversation["id"])[0]["id"] == event_id


def test_only_proactor_connection_reset_disconnects_are_filtered():
    from video_fission.main import _is_expected_client_disconnect

    disconnect = ConnectionResetError(10054, "connection reset")
    assert _is_expected_client_disconnect(
        {
            "exception": disconnect,
            "handle": "Handle _ProactorBasePipeTransport._call_connection_lost()",
        }
    )
    assert not _is_expected_client_disconnect(
        {
            "exception": disconnect,
            "handle": "Handle other_callback()",
        }
    )
    assert not _is_expected_client_disconnect(
        {
            "exception": RuntimeError("unexpected"),
            "handle": "Handle _ProactorBasePipeTransport._call_connection_lost()",
        }
    )


def test_skill_crud_and_toggle(test_settings):
    manager = SkillManager(
        test_settings.skills_dir,
        test_settings.bundled_skills_dir,
        test_settings.disabled_skills_dir,
    )
    created = manager.create(
        {
            "slug": "test-workflow",
            "name": "Test Workflow",
            "description": "Exercise skill persistence",
            "body": "## Steps\n\n1. Verify.",
        }
    )
    assert created["enabled"] is True
    assert (test_settings.skills_dir / "test-workflow" / "SKILL.md").is_file()

    disabled = manager.toggle("test-workflow", False)
    assert disabled["enabled"] is False
    assert (test_settings.disabled_skills_dir / "test-workflow" / "SKILL.md").is_file()

    enabled = manager.toggle("test-workflow", True)
    assert enabled["enabled"] is True
    manager.delete("test-workflow")
    assert not (test_settings.skills_dir / "test-workflow").exists()


def test_invalid_skill_does_not_leave_an_empty_directory(test_settings):
    manager = SkillManager(
        test_settings.skills_dir,
        test_settings.bundled_skills_dir,
        test_settings.disabled_skills_dir,
    )
    with pytest.raises(SkillValidationError):
        manager.create(
            {
                "slug": "invalid-skill",
                "name": "Invalid Skill",
                "description": "This should fail",
                "body": "   ",
            }
        )
    assert not (test_settings.skills_dir / "invalid-skill").exists()


def test_dynamic_tool_creates_artifact(test_settings):
    db = Database(test_settings.database_path)
    db.initialize()
    conversation = db.create_conversation({"cwd": str(test_settings.workspace)})
    registry = build_registry(db, test_settings.workspace, test_settings.artifacts_dir)
    result = registry.execute(
        "create_video_brief",
        {
            "topic": "Test Topic",
            "audience": "Test audience",
            "duration_seconds": 30,
        },
        {"conversation_id": conversation["id"]},
    )
    assert result["success"] is True
    path = Path(result["contentItems"][0]["text"].split("：", 1)[1])
    assert path.is_file()
    assert "Test Topic" in path.read_text(encoding="utf-8")

    second = registry.execute(
        "create_video_brief",
        {"topic": "Test Topic", "duration_seconds": 15},
        {"conversation_id": conversation["id"]},
    )
    second_path = Path(second["contentItems"][0]["text"].split("：", 1)[1])
    assert second_path != path
    assert second_path.is_file()
    assert "15 秒" in second_path.read_text(encoding="utf-8")
    assert "| 15-15s |" not in second_path.read_text(encoding="utf-8")
    assert "18s" not in second_path.read_text(encoding="utf-8")


def test_send_rejects_a_second_turn_even_when_the_lock_is_free(test_settings):
    db = Database(test_settings.database_path)
    skills = SkillManager(
        test_settings.skills_dir,
        test_settings.bundled_skills_dir,
        test_settings.disabled_skills_dir,
    )
    tools = build_registry(db, test_settings.workspace, test_settings.artifacts_dir)
    hub = CodexHub(test_settings, db, skills, tools)
    hub.initialize()
    conversation = hub.create_conversation({})
    hub.db.update_conversation(conversation["id"], {"status": "running"})
    try:
        with pytest.raises(ConversationBusy):
            hub.send_message(conversation["id"], {"text": "second turn"})
    finally:
        hub.shutdown()


def test_attachment_paths_are_limited_to_application_uploads(test_settings):
    db = Database(test_settings.database_path)
    skills = SkillManager(
        test_settings.skills_dir,
        test_settings.bundled_skills_dir,
        test_settings.disabled_skills_dir,
    )
    tools = build_registry(db, test_settings.workspace, test_settings.artifacts_dir)
    hub = CodexHub(test_settings, db, skills, tools)
    hub.initialize()
    upload = test_settings.uploads_dir / "safe.txt"
    upload.write_text("safe", encoding="utf-8")
    try:
        validated = hub._validated_files([{"id": "safe", "name": "../safe.txt", "path": str(upload)}])
        assert validated[0]["path"] == str(upload.resolve())
        assert validated[0]["name"] == "safe.txt"
        with pytest.raises(ValueError):
            hub._validated_files([{"name": "passwd", "path": "/etc/passwd"}])
    finally:
        hub.shutdown()


def test_runtime_exit_marks_running_conversation_failed(test_settings):
    db = Database(test_settings.database_path)
    skills = SkillManager(
        test_settings.skills_dir,
        test_settings.bundled_skills_dir,
        test_settings.disabled_skills_dir,
    )
    tools = build_registry(db, test_settings.workspace, test_settings.artifacts_dir)
    hub = CodexHub(test_settings, db, skills, tools)
    hub.initialize()
    conversation = hub.create_conversation({})
    hub.db.update_conversation(
        conversation["id"],
        {"status": "running", "turn_generation": 2},
    )
    try:
        hub._on_runtime_exit(conversation["id"], object(), "process exited")
        failed = hub.get_conversation(conversation["id"])
        assert failed["status"] == "failed"
        assert "process exited" in failed["last_error"]
        assert hub.db.list_events(conversation["id"])[-1]["kind"] == "error"
    finally:
        hub.shutdown()


def test_stale_turn_completion_does_not_end_the_new_turn(test_settings):
    db = Database(test_settings.database_path)
    skills = SkillManager(
        test_settings.skills_dir,
        test_settings.bundled_skills_dir,
        test_settings.disabled_skills_dir,
    )
    tools = build_registry(db, test_settings.workspace, test_settings.artifacts_dir)
    hub = CodexHub(test_settings, db, skills, tools)
    hub.initialize()
    conversation = hub.create_conversation({})
    hub.db.update_conversation(
        conversation["id"],
        {"status": "running", "active_turn_id": "new-turn"},
    )
    try:
        hub._on_notification(
            conversation["id"],
            "turn/completed",
            {"turn": {"id": "old-turn", "status": "completed"}},
        )
        current = hub.get_conversation(conversation["id"])
        assert current["status"] == "running"
        assert current["active_turn_id"] == "new-turn"
    finally:
        hub.shutdown()


def test_initialize_recovers_orphaned_runs_and_interactions(test_settings):
    db = Database(test_settings.database_path)
    skills = SkillManager(
        test_settings.skills_dir,
        test_settings.bundled_skills_dir,
        test_settings.disabled_skills_dir,
    )
    tools = build_registry(db, test_settings.workspace, test_settings.artifacts_dir)
    hub = CodexHub(test_settings, db, skills, tools)
    hub.initialize()
    conversation = hub.create_conversation({})
    hub.db.update_conversation(
        conversation["id"],
        {"status": "running", "active_turn_id": "orphaned-turn"},
    )
    approval = hub.db.create_approval(
        conversation["id"],
        "request-1",
        "item/commandExecution/requestApproval",
        {"kind": "command"},
        {},
    )
    clarification = hub.db.create_clarification(
        conversation["id"],
        "request-2",
        "question",
        [{"id": "answer", "question": "Question"}],
    )
    try:
        hub.initialize()
        recovered = hub.get_conversation(conversation["id"])
        assert recovered["status"] == "failed"
        assert recovered["active_turn_id"] is None
        assert not recovered["pending_approvals"]
        assert not recovered["pending_clarifications"]
        assert hub.db.get_approval(approval["id"])["decision"] == "cancel"
        assert hub.db.get_clarification(clarification["id"])["answers"] == {}
        assert hub.db.list_events(conversation["id"])[-1]["kind"] == "runtime.recovered"
    finally:
        hub.shutdown()


def test_deleting_a_conversation_removes_its_uploaded_files_and_artifacts(test_settings):
    db = Database(test_settings.database_path)
    skills = SkillManager(
        test_settings.skills_dir,
        test_settings.bundled_skills_dir,
        test_settings.disabled_skills_dir,
    )
    tools = build_registry(db, test_settings.workspace, test_settings.artifacts_dir)
    hub = CodexHub(test_settings, db, skills, tools)
    hub.initialize()
    conversation = hub.create_conversation({})
    upload = test_settings.uploads_dir / "cleanup.txt"
    upload.write_text("temporary", encoding="utf-8")
    db.add_item(
        conversation["id"],
        {
            "item_type": "userMessage",
            "role": "user",
            "status": "completed",
            "payload": {"files": [{"name": "cleanup.txt", "path": str(upload.resolve())}]},
        },
    )
    artifact_dir = test_settings.artifacts_dir / conversation["id"]
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "brief.md").write_text("artifact", encoding="utf-8")
    try:
        hub.delete(conversation["id"])
        assert not upload.exists()
        assert not artifact_dir.exists()
    finally:
        hub.shutdown()


def test_refresh_auth_tokens_reads_codex_home(monkeypatch, tmp_path):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text(
        '{"tokens":{"access_token":"token-value","account_id":"account-value","plan_type":"plus"}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    payload = CodexHub._refresh_auth_tokens()
    assert payload == {
        "accessToken": "token-value",
        "chatgptAccountId": "account-value",
        "chatgptPlanType": "plus",
    }


def test_initialize_removes_old_unreferenced_uploads(test_settings):
    orphan = test_settings.uploads_dir / "orphan.txt"
    orphan.write_text("orphan", encoding="utf-8")
    orphan_artifact = test_settings.artifacts_dir / "missing-conversation"
    orphan_artifact.mkdir()
    old = time.time() - 2 * 86_400
    os.utime(orphan, (old, old))
    os.utime(orphan_artifact, (old, old))
    db = Database(test_settings.database_path)
    skills = SkillManager(
        test_settings.skills_dir,
        test_settings.bundled_skills_dir,
        test_settings.disabled_skills_dir,
    )
    tools = build_registry(db, test_settings.workspace, test_settings.artifacts_dir)
    hub = CodexHub(test_settings, db, skills, tools)
    try:
        hub.initialize()
        assert not orphan.exists()
        assert not orphan_artifact.exists()
    finally:
        hub.shutdown()


def test_idle_runtime_is_closed_but_running_runtime_is_kept(test_settings):
    db = Database(test_settings.database_path)
    skills = SkillManager(
        test_settings.skills_dir,
        test_settings.bundled_skills_dir,
        test_settings.disabled_skills_dir,
    )
    tools = build_registry(db, test_settings.workspace, test_settings.artifacts_dir)
    hub = CodexHub(test_settings, db, skills, tools)
    hub.initialize()
    idle = hub.create_conversation({})
    running = hub.create_conversation({})
    db.update_conversation(running["id"], {"status": "running"})
    idle_runtime = Mock(last_activity=time.time() - RUNTIME_IDLE_SECONDS - 1)
    running_runtime = Mock(last_activity=time.time() - RUNTIME_IDLE_SECONDS - 1)
    hub._runtimes[idle["id"]] = idle_runtime
    hub._runtimes[running["id"]] = running_runtime
    try:
        hub._close_idle_runtimes()
        idle_runtime.close.assert_called_once()
        running_runtime.close.assert_not_called()
        assert idle["id"] not in hub._runtimes
        assert running["id"] in hub._runtimes
    finally:
        hub.shutdown()


def test_hub_capabilities_expose_skill_and_tool_contracts(test_settings):
    db = Database(test_settings.database_path)
    skills = SkillManager(
        test_settings.skills_dir,
        test_settings.bundled_skills_dir,
        test_settings.disabled_skills_dir,
    )
    tools = build_registry(db, test_settings.workspace, test_settings.artifacts_dir)
    hub = CodexHub(test_settings, db, skills, tools)
    hub.initialize()
    try:
        payload = hub.capabilities()
        assert "available" in payload["runtime"]
        assert any(skill["slug"] == "video-brief" for skill in payload["skills"])
        assert {
            "workspace_snapshot",
            "read_workspace_file",
            "conversation_metrics",
            "create_video_brief",
        }.issubset({tool["name"] for tool in payload["tools"]})
    finally:
        hub.shutdown()


def test_ugc_skill_pipeline_is_bundled_and_shares_reference_library(test_settings):
    manager = SkillManager(
        test_settings.skills_dir,
        test_settings.bundled_skills_dir,
        test_settings.disabled_skills_dir,
    )
    bundled = {skill["slug"]: skill for skill in manager.list() if skill["source"] == "bundled"}
    expected = {
        "creative-intent-script-skeleton",
        "universal-ugc-video-script-generation",
        "video-references",
    }
    assert expected.issubset(bundled)

    for slug in ("creative-intent-script-skeleton", "universal-ugc-video-script-generation"):
        assert "$video-references" in bundled[slug]["content"]

    reference_root = Path(bundled["video-references"]["path"]).parent / "references"
    scripts = sorted(path for path in reference_root.glob("*/*.md") if path.is_file())
    assert len(scripts) == 13

    index = (reference_root / "index.md").read_text(encoding="utf-8")
    assert index.startswith("# Video References Catalog")
    assert "../SKILL.md" in index
    for stale in ("read_file", "list_files", "write_file", "edit_file"):
        assert stale not in index

    by_category: dict[str, int] = {}
    for script in scripts:
        category = script.parent.name
        by_category[category] = by_category.get(category, 0) + 1
        relative = script.relative_to(reference_root).as_posix()
        assert f"`{relative}`" in index
        text = script.read_text(encoding="utf-8")
        first_line = text.lstrip("\ufeff").splitlines()[0]
        assert re.match(r"^\d+\..+[，,].+[，,]", first_line)
        assert text.count("\n---\n") >= 2
    for category, count in by_category.items():
        assert f"### 【{category}】（共 {count} 篇）" in index
    assert by_category == {"吹风机": 2, "除吹雪机": 11}

    library = ReferenceLibrary(test_settings.project_root)
    blower_refs = library.retrieve("便携吹风机大风力吹落叶")
    snow_refs = library.retrieve("手持热能吹雪机车窗除雪")
    assert len(blower_refs) == 4
    assert len({item["path"] for item in blower_refs}) == 4
    assert [item["category"] for item in blower_refs[:2]] == ["吹风机", "吹风机"]
    assert [item["category"] for item in snow_refs[:3]] == ["除吹雪机"] * 3
    assert snow_refs[3]["category"] == "吹风机"
    assert blower_refs[3]["category"] == "除吹雪机"

    db = Database(test_settings.database_path)
    tools = build_registry(db, test_settings.workspace, test_settings.artifacts_dir)
    hub = CodexHub(test_settings, db, manager, tools)
    try:
        instructions = hub._system_instructions({})
        for skill in expected:
            assert skill in instructions
        assert "骨架尚未明确时先用" in instructions
        assert "用户已确认骨架时直接使用" in instructions
    finally:
        hub.shutdown()
