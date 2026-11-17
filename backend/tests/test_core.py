from __future__ import annotations

from pathlib import Path

from video_fission.db import Database
from video_fission.service import CodexHub
from video_fission.skills import SkillManager
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
        assert payload["runtime"]["available"] is True
        assert any(skill["slug"] == "video-brief" for skill in payload["skills"])
        assert {
            "workspace_snapshot",
            "read_workspace_file",
            "conversation_metrics",
            "create_video_brief",
        }.issubset({tool["name"] for tool in payload["tools"]})
    finally:
        hub.shutdown()
