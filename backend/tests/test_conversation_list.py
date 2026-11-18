from __future__ import annotations

from video_fission.db import Database
from video_fission.service import CodexHub
from video_fission.skills import SkillManager
from video_fission.tools import build_registry


def _conversation(db: Database, title: str, workspace: str) -> dict:
    conversation = db.create_conversation(
        {
            "title": title,
            "cwd": workspace,
            "model": "deepseek-flash",
            "reasoning_effort": "low",
        }
    )
    return conversation


def test_conversation_list_returns_lightweight_aggregate_states(test_settings):
    db = Database(test_settings.database_path)
    db.initialize()
    workspace = str(test_settings.workspace)

    empty = _conversation(db, "空草稿", workspace)
    completed = _conversation(db, "已完成", workspace)
    db.add_item(
        completed["id"],
        {
            "item_type": "userMessage",
            "role": "user",
            "status": "completed",
            "payload": {"text": "完成一个任务"},
        },
    )
    warning = _conversation(db, "工具警告", workspace)
    db.add_item(
        warning["id"],
        {
            "item_type": "dynamicToolCall",
            "status": "completed",
            "payload": {
                "type": "dynamicToolCall",
                "tool": "read_workspace_file",
                "status": "failed",
                "success": False,
            },
        },
    )
    failed = _conversation(db, "整体失败", workspace)
    db.update_conversation(
        failed["id"],
        {"status": "failed", "last_error": "runtime unavailable"},
    )
    running = _conversation(db, "运行中", workspace)
    db.update_conversation(running["id"], {"status": "running"})

    page = db.list_conversations(archived=False, limit=50)
    items = {item["id"]: item for item in page["items"]}

    assert items[empty["id"]]["list_state"] == "empty"
    assert items[completed["id"]]["list_state"] == "completed"
    assert items[warning["id"]]["list_state"] == "warning"
    assert items[warning["id"]]["has_warnings"] is True
    assert items[failed["id"]]["list_state"] == "failed"
    assert items[running["id"]]["list_state"] == "running"
    assert page["next_cursor"] is None
    assert "cwd" not in items[completed["id"]]
    assert "developer_instructions" not in items[completed["id"]]
    assert "item_count" not in items[completed["id"]]


def test_conversation_list_cursor_pagination_is_stable_and_complete(test_settings):
    db = Database(test_settings.database_path)
    db.initialize()
    workspace = str(test_settings.workspace)
    for index in range(7):
        _conversation(db, f"分页会话 {index}", workspace)

    first = db.list_conversations(archived=False, limit=3)
    second = db.list_conversations(
        archived=False,
        limit=3,
        cursor=first["next_cursor"],
    )
    third = db.list_conversations(
        archived=False,
        limit=3,
        cursor=second["next_cursor"],
    )

    ids = [
        item["id"]
        for page in (first, second, third)
        for item in page["items"]
    ]
    assert len(ids) == 7
    assert len(set(ids)) == 7
    assert first["next_cursor"]
    assert second["next_cursor"]
    assert third["next_cursor"] is None


def test_conversation_list_filters_archive_and_searches_title_or_error(test_settings):
    db = Database(test_settings.database_path)
    db.initialize()
    workspace = str(test_settings.workspace)
    active = _conversation(db, "吹雪机拍摄方案", workspace)
    archived = _conversation(db, "旧归档会话", workspace)
    failed = _conversation(db, "运行异常", workspace)
    db.update_conversation(
        archived["id"],
        {"archived_at": "2026-11-18T00:00:00+00:00", "status": "archived"},
    )
    db.update_conversation(
        failed["id"],
        {"status": "failed", "last_error": "sandbox ACL lock"},
    )

    active_page = db.list_conversations(archived=False, limit=50)
    archived_page = db.list_conversations(archived=True, limit=50)
    title_search = db.list_conversations(archived=False, query="吹雪机", limit=50)
    error_search = db.list_conversations(archived=False, query="ACL", limit=50)

    assert {item["id"] for item in active_page["items"]} == {
        active["id"],
        failed["id"],
    }
    assert [item["id"] for item in archived_page["items"]] == [archived["id"]]
    assert [item["id"] for item in title_search["items"]] == [active["id"]]
    assert [item["id"] for item in error_search["items"]] == [failed["id"]]


def test_fallback_title_is_replaced_by_codex_semantic_name(test_settings):
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
    try:
        fallback = hub._fallback_title("把吹雪机卖点拆成短视频\n补充说明", [])
        assert fallback == "把吹雪机卖点拆成短视频"
        hub._maybe_retitle(conversation, conversation["id"], "把吹雪机卖点拆成短视频", [])
        hub._on_notification(
            conversation["id"],
            "thread/name/updated",
            {"name": "吹雪机冬季清雪 UGC 脚本"},
        )
        updated = hub.get_conversation(conversation["id"])
        assert updated["title"] == "吹雪机冬季清雪 UGC 脚本"
    finally:
        hub.shutdown()
