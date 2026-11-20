from __future__ import annotations

from video_fission.api.media_cache import (
    IMMUTABLE_MEDIA_CACHE_CONTROL,
    MUTABLE_MEDIA_CACHE_CONTROL,
    media_cache_headers,
    media_not_modified_headers,
)
from video_fission.db import Database
from video_fission.service import CodexHub


def test_media_cache_headers_are_private_and_distinct() -> None:
    assert media_cache_headers() == {
        "Cache-Control": MUTABLE_MEDIA_CACHE_CONTROL,
        "Vary": "Authorization, Cookie",
    }
    assert media_cache_headers(immutable=True) == {
        "Cache-Control": IMMUTABLE_MEDIA_CACHE_CONTROL,
        "Vary": "Authorization, Cookie",
    }
    assert "no-store" not in MUTABLE_MEDIA_CACHE_CONTROL


def test_media_etag_condition_matches_strong_and_weak_tags() -> None:
    headers = {
        "etag": '"abc123"',
        "cache-control": MUTABLE_MEDIA_CACHE_CONTROL,
        "content-length": "1024",
    }

    assert media_not_modified_headers('W/"abc123"', headers) == {
        "etag": '"abc123"',
        "cache-control": MUTABLE_MEDIA_CACHE_CONTROL,
    }
    assert media_not_modified_headers('"other"', headers) is None


def test_timeline_pages_and_incremental_updates(tmp_path) -> None:
    db = Database(tmp_path / "paging.db")
    db.initialize()
    conversation = db.create_conversation(
        {"title": "paging", "cwd": str(tmp_path)}
    )
    conversation_id = conversation["id"]
    items = [
        db.add_item(
            conversation_id,
            {
                "item_type": "userMessage",
                "role": "user",
                "payload": {"text": f"message-{index}"},
            },
        )
        for index in range(1, 4)
    ]
    with db.transaction() as connection:
        for index, item in enumerate(items, start=1):
            created_at = f"2026-01-01T00:00:0{index}+00:00"
            connection.execute(
                "UPDATE items SET created_at = ?, updated_at = ? WHERE id = ?",
                (created_at, created_at, item["id"]),
            )

    first_page = db.list_items_page(conversation_id, limit=2)

    assert [item["payload"]["text"] for item in first_page["items"]] == [
        "message-2",
        "message-3",
    ]
    assert first_page["has_more"] is True
    assert first_page["next_cursor"]

    second_page = db.list_items_page(
        conversation_id,
        limit=2,
        cursor=first_page["next_cursor"],
    )

    assert [item["payload"]["text"] for item in second_page["items"]] == [
        "message-1",
    ]
    assert second_page["has_more"] is False

    with db.transaction() as connection:
        connection.execute(
            "UPDATE items SET updated_at = ? WHERE id = ?",
            ("2026-01-01T00:00:04+00:00", items[0]["id"]),
        )

    updated_page = db.list_items_page(conversation_id, limit=1)
    assert [item["id"] for item in updated_page["items"]] == [items[0]["id"]]

    updates = db.list_items_updated_since(
        conversation_id,
        "2026-01-01T00:00:03+00:00",
    )

    assert [item["id"] for item in updates] == [items[0]["id"]]

    db.delete_item(items[1]["id"], conversation_id)
    db.append_event(
        conversation_id,
        "item_deleted",
        {"item_id": items[1]["id"], "item_type": "video_segments"},
    )

    deleted_ids = db.list_deleted_item_ids_since(
        conversation_id,
        "2026-01-01T00:00:04+00:00",
    )

    assert deleted_ids == [items[1]["id"]]


def test_incremental_updates_do_not_silently_truncate(tmp_path) -> None:
    db = Database(tmp_path / "incremental.db")
    db.initialize()
    conversation_id = db.create_conversation(
        {"title": "incremental", "cwd": str(tmp_path)}
    )["id"]
    values = [
        (
            f"bulk-{index:04d}",
            conversation_id,
            None,
            "agentMessage",
            "assistant",
            "completed",
            "{}",
            "2026-01-01T00:00:01+00:00",
            f"2026-01-01T00:00:01.{index:06d}+00:00",
        )
        for index in range(2005)
    ]
    with db.transaction() as connection:
        connection.executemany(
            """
            INSERT INTO items (
                id, conversation_id, codex_item_id, item_type, role, status,
                payload, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )

    updates = db.list_items_updated_since(
        conversation_id,
        "2026-01-01T00:00:00+00:00",
    )

    assert len(updates) == 2005


def test_conversation_detail_omits_unused_tool_runs_by_default() -> None:
    class FakeDatabase:
        def list_items_page(self, conversation_id, *, limit, cursor):
            assert conversation_id == "conv"
            return {"items": [], "next_cursor": None, "has_more": False}

        def latest_event_id(self, conversation_id):
            return 12

        def pending_approvals(self, conversation_id):
            return []

        def pending_clarifications(self, conversation_id):
            return []

        def list_tool_runs(self, conversation_id):
            raise AssertionError("tool runs should be opt-in")

        def list_items_updated_since(self, conversation_id, since):
            return []

        def list_deleted_item_ids_since(self, conversation_id, since):
            return ["deleted-item"]

    hub = CodexHub.__new__(CodexHub)
    hub.db = FakeDatabase()
    hub._close_idle_runtimes = lambda: None
    hub._auto_approve_pending = lambda conversation_id: None
    hub._conversation = lambda conversation_id, user_id=None: {
        "id": conversation_id,
        "title": "detail",
        "status": "idle",
        "user_id": None,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }

    detail = hub.get_conversation("conv")

    assert detail["items"] == []
    assert detail["latest_event_id"] == 12
    assert "tool_runs" not in detail

    updates = hub.get_conversation_updates(
        "conv",
        "2026-01-01T00:00:00+00:00",
    )

    assert updates["deleted_item_ids"] == ["deleted-item"]
