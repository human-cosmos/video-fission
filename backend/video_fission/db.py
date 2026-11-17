from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, separators=(",", ":"))


def _load(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._write_lock = threading.RLock()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._write_lock:
            connection = self.connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        connection = self.connect()
        try:
            return connection.execute(sql, params).fetchall()
        finally:
            connection.close()

    def query_one(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        connection = self.connect()
        try:
            return connection.execute(sql, params).fetchone()
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'idle',
                    cwd TEXT NOT NULL,
                    model TEXT,
                    reasoning_effort TEXT,
                    sandbox_mode TEXT NOT NULL DEFAULT 'read-only',
                    approval_policy TEXT NOT NULL DEFAULT 'on-request',
                    developer_instructions TEXT NOT NULL DEFAULT '',
                    codex_thread_id TEXT,
                    active_turn_id TEXT,
                    turn_generation INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    archived_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_conversations_updated
                    ON conversations(updated_at DESC);

                CREATE TABLE IF NOT EXISTS items (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    codex_item_id TEXT,
                    item_type TEXT NOT NULL,
                    role TEXT,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_items_codex
                    ON items(conversation_id, codex_item_id)
                    WHERE codex_item_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_items_conversation_created
                    ON items(conversation_id, created_at, id);

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_conversation
                    ON events(conversation_id, id);

                CREATE TABLE IF NOT EXISTS approvals (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    codex_request_id TEXT NOT NULL,
                    method TEXT NOT NULL,
                    request TEXT NOT NULL,
                    params TEXT NOT NULL,
                    decision TEXT,
                    reason TEXT,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_approvals_pending
                    ON approvals(conversation_id, decision, created_at);

                CREATE TABLE IF NOT EXISTS clarifications (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    codex_request_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    questions TEXT NOT NULL,
                    answers TEXT,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_clarifications_pending
                    ON clarifications(conversation_id, answers, created_at);

                CREATE TABLE IF NOT EXISTS tool_runs (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    turn_id TEXT,
                    call_id TEXT,
                    tool_name TEXT NOT NULL,
                    arguments TEXT NOT NULL,
                    result TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_tool_runs_conversation
                    ON tool_runs(conversation_id, created_at);
                """
            )

    def create_conversation(self, payload: dict[str, Any]) -> dict[str, Any]:
        conversation_id = uuid.uuid4().hex
        now = utc_now()
        values = {
            "id": conversation_id,
            "title": str(payload.get("title") or "新对话").strip() or "新对话",
            "status": "idle",
            "cwd": payload["cwd"],
            "model": payload.get("model"),
            "reasoning_effort": payload.get("reasoning_effort"),
            "sandbox_mode": "read-only",
            "approval_policy": "on-request",
            "developer_instructions": payload.get("developer_instructions") or "",
            "codex_thread_id": None,
            "active_turn_id": None,
            "turn_generation": 0,
            "last_error": None,
            "archived_at": None,
            "created_at": now,
            "updated_at": now,
        }
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO conversations (
                    id, title, status, cwd, model, reasoning_effort, sandbox_mode,
                    approval_policy, developer_instructions, codex_thread_id,
                    active_turn_id, turn_generation, last_error, archived_at,
                    created_at, updated_at
                ) VALUES (
                    :id, :title, :status, :cwd, :model, :reasoning_effort, :sandbox_mode,
                    :approval_policy, :developer_instructions, :codex_thread_id,
                    :active_turn_id, :turn_generation, :last_error, :archived_at,
                    :created_at, :updated_at
                )
                """,
                values,
            )
        return self.get_conversation(conversation_id) or {}

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        row = self.query_one("SELECT * FROM conversations WHERE id = ?", (conversation_id,))
        return dict(row) if row else None

    def list_conversations(self, include_archived: bool = False) -> list[dict[str, Any]]:
        where = "" if include_archived else "WHERE archived_at IS NULL"
        rows = self.query(
            f"""
            SELECT
                c.*,
                (SELECT COUNT(*) FROM items i WHERE i.conversation_id = c.id) AS item_count
            FROM conversations c
            {where}
            ORDER BY c.updated_at DESC
            """
        )
        return [dict(row) for row in rows]

    def update_conversation(self, conversation_id: str, fields: dict[str, Any]) -> None:
        allowed = {
            "title",
            "status",
            "model",
            "reasoning_effort",
            "developer_instructions",
            "codex_thread_id",
            "active_turn_id",
            "turn_generation",
            "last_error",
            "archived_at",
        }
        values = {key: value for key, value in fields.items() if key in allowed}
        values["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self.transaction() as connection:
            connection.execute(
                f"UPDATE conversations SET {assignments} WHERE id = ?",
                (*values.values(), conversation_id),
            )

    def delete_conversation(self, conversation_id: str) -> None:
        with self.transaction() as connection:
            connection.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))

    def append_event(self, conversation_id: str, kind: str, payload: Any) -> int:
        now = utc_now()
        with self.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO events (conversation_id, kind, payload, created_at) VALUES (?, ?, ?, ?)",
                (conversation_id, kind, _json(payload), now),
            )
            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )
            return int(cursor.lastrowid)

    def list_events(self, conversation_id: str, after_id: int = 0, limit: int = 300) -> list[dict[str, Any]]:
        rows = self.query(
            """
            SELECT * FROM events
            WHERE conversation_id = ? AND id > ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (conversation_id, after_id, limit),
        )
        return [
            {
                "id": row["id"],
                "kind": row["kind"],
                "payload": _load(row["payload"], {}),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def list_items(self, conversation_id: str) -> list[dict[str, Any]]:
        rows = self.query(
            "SELECT * FROM items WHERE conversation_id = ? ORDER BY created_at ASC, id ASC",
            (conversation_id,),
        )
        return [
            {
                "id": row["id"],
                "conversation_id": row["conversation_id"],
                "codex_item_id": row["codex_item_id"],
                "item_type": row["item_type"],
                "role": row["role"],
                "status": row["status"],
                "payload": _load(row["payload"], {}),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def add_item(self, conversation_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        item_id = uuid.uuid4().hex
        now = utc_now()
        values = {
            "id": item_id,
            "conversation_id": conversation_id,
            "codex_item_id": fields.get("codex_item_id"),
            "item_type": fields.get("item_type") or "raw",
            "role": fields.get("role"),
            "status": fields.get("status") or "completed",
            "payload": _json(fields.get("payload") or {}),
            "created_at": now,
            "updated_at": now,
        }
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO items (
                    id, conversation_id, codex_item_id, item_type, role, status,
                    payload, created_at, updated_at
                ) VALUES (
                    :id, :conversation_id, :codex_item_id, :item_type, :role, :status,
                    :payload, :created_at, :updated_at
                )
                """,
                values,
            )
            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )
        return self.get_item(item_id) or {}

    def get_item(self, item_id: str) -> dict[str, Any] | None:
        row = self.query_one("SELECT * FROM items WHERE id = ?", (item_id,))
        if not row:
            return None
        value = dict(row)
        value["payload"] = _load(value["payload"], {})
        return value

    def get_codex_item(self, conversation_id: str, codex_item_id: str) -> dict[str, Any] | None:
        row = self.query_one(
            "SELECT * FROM items WHERE conversation_id = ? AND codex_item_id = ?",
            (conversation_id, codex_item_id),
        )
        if not row:
            return None
        value = dict(row)
        value["payload"] = _load(value["payload"], {})
        return value

    def upsert_codex_item(
        self,
        conversation_id: str,
        codex_item_id: str,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        existing = self.get_codex_item(conversation_id, codex_item_id)
        now = utc_now()
        if existing:
            payload = {**(existing.get("payload") or {}), **(fields.get("payload") or {})}
            with self.transaction() as connection:
                connection.execute(
                    """
                    UPDATE items
                    SET item_type = ?, role = ?, status = ?, payload = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        fields.get("item_type") or existing["item_type"],
                        fields.get("role", existing.get("role")),
                        fields.get("status") or existing["status"],
                        _json(payload),
                        now,
                        existing["id"],
                    ),
                )
            return self.get_item(existing["id"]) or {}
        return self.add_item(
            conversation_id,
            {**fields, "codex_item_id": codex_item_id},
        )

    def copy_items(self, source_id: str, target_id: str) -> None:
        now = utc_now()
        with self.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM items WHERE conversation_id = ? ORDER BY created_at ASC",
                (source_id,),
            ).fetchall()
            for row in rows:
                connection.execute(
                    """
                    INSERT INTO items (
                        id, conversation_id, codex_item_id, item_type, role, status,
                        payload, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        uuid.uuid4().hex,
                        target_id,
                        row["codex_item_id"],
                        row["item_type"],
                        row["role"],
                        row["status"],
                        row["payload"],
                        row["created_at"],
                        now,
                    ),
                )

    def pending_approvals(self, conversation_id: str) -> list[dict[str, Any]]:
        rows = self.query(
            """
            SELECT * FROM approvals
            WHERE conversation_id = ? AND decision IS NULL
            ORDER BY created_at ASC
            """,
            (conversation_id,),
        )
        return [self._approval(row) for row in rows]

    def get_approval(self, approval_id: str) -> dict[str, Any] | None:
        row = self.query_one("SELECT * FROM approvals WHERE id = ?", (approval_id,))
        return self._approval(row) if row else None

    @staticmethod
    def _approval(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["request"] = _load(value.get("request"), {})
        value["params"] = _load(value.get("params"), {})
        return value

    def create_approval(
        self,
        conversation_id: str,
        request_id: str,
        method: str,
        request: dict[str, Any],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        approval_id = uuid.uuid4().hex
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO approvals (
                    id, conversation_id, codex_request_id, method, request, params, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    conversation_id,
                    request_id,
                    method,
                    _json(request),
                    _json(params),
                    utc_now(),
                ),
            )
        return self.get_approval(approval_id) or {}

    def resolve_approval(self, approval_id: str, decision: str, reason: str | None = None) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE approvals SET decision = ?, reason = ?, resolved_at = ?
                WHERE id = ? AND decision IS NULL
                """,
                (decision, reason, utc_now(), approval_id),
            )

    def pending_clarifications(self, conversation_id: str) -> list[dict[str, Any]]:
        rows = self.query(
            """
            SELECT * FROM clarifications
            WHERE conversation_id = ? AND answers IS NULL
            ORDER BY created_at ASC
            """,
            (conversation_id,),
        )
        return [self._clarification(row) for row in rows]

    def get_clarification(self, clarification_id: str) -> dict[str, Any] | None:
        row = self.query_one("SELECT * FROM clarifications WHERE id = ?", (clarification_id,))
        return self._clarification(row) if row else None

    @staticmethod
    def _clarification(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["questions"] = _load(value.get("questions"), [])
        value["answers"] = _load(value.get("answers"), None)
        return value

    def create_clarification(
        self,
        conversation_id: str,
        request_id: str,
        kind: str,
        questions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        clarification_id = uuid.uuid4().hex
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO clarifications (
                    id, conversation_id, codex_request_id, kind, questions, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    clarification_id,
                    conversation_id,
                    request_id,
                    kind,
                    _json(questions),
                    utc_now(),
                ),
            )
        return self.get_clarification(clarification_id) or {}

    def resolve_clarification(self, clarification_id: str, answers: dict[str, Any]) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE clarifications SET answers = ?, resolved_at = ?
                WHERE id = ? AND answers IS NULL
                """,
                (_json(answers), utc_now(), clarification_id),
            )

    def record_tool_run(
        self,
        conversation_id: str,
        turn_id: str | None,
        call_id: str | None,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO tool_runs (
                    id, conversation_id, turn_id, call_id, tool_name,
                    arguments, result, success, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    conversation_id,
                    turn_id,
                    call_id,
                    tool_name,
                    _json(arguments),
                    _json(result),
                    1 if result.get("success") else 0,
                    utc_now(),
                ),
            )

    def list_tool_runs(self, conversation_id: str) -> list[dict[str, Any]]:
        rows = self.query(
            """
            SELECT * FROM tool_runs
            WHERE conversation_id = ?
            ORDER BY created_at ASC
            """,
            (conversation_id,),
        )
        return [
            {
                **{key: row[key] for key in row.keys() if key not in {"arguments", "result", "success"}},
                "arguments": _load(row["arguments"], {}),
                "result": _load(row["result"], {}),
                "success": bool(row["success"]),
            }
            for row in rows
        ]
