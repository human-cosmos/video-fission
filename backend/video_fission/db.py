from __future__ import annotations

import base64
import binascii
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


def _row_get(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


def encode_conversation_cursor(updated_at: str, conversation_id: str) -> str:
    value = json.dumps([updated_at, conversation_id], separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def decode_conversation_cursor(cursor: str) -> tuple[str, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        updated_at, conversation_id = value
    except (ValueError, TypeError, binascii.Error, json.JSONDecodeError) as exc:
        raise ValueError("无效的会话游标") from exc
    if not isinstance(updated_at, str) or not isinstance(conversation_id, str):
        raise ValueError("无效的会话游标")
    return updated_at, conversation_id


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

                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user',
                    display_name TEXT NOT NULL DEFAULT '',
                    sso_user_id INTEGER,
                    area_id INTEGER,
                    token_version INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username
                    ON users(username COLLATE NOCASE);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_users_sso_user_id
                    ON users(sso_user_id)
                    WHERE sso_user_id IS NOT NULL;

                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    public_json TEXT NOT NULL,
                    secrets_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    user_id TEXT,
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

                CREATE TABLE IF NOT EXISTS app_settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    public_json TEXT NOT NULL,
                    secrets_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS video_uploads (
                    id TEXT PRIMARY KEY,
                    user_id TEXT,
                    name TEXT NOT NULL,
                    path TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    content_type TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    width INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    fps REAL NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS productions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT,
                    title TEXT NOT NULL,
                    brief TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_stage TEXT NOT NULL,
                    error TEXT,
                    warnings TEXT NOT NULL,
                    source_video TEXT NOT NULL,
                    decomposition TEXT,
                    intent TEXT,
                    skeleton TEXT,
                    script TEXT,
                    clips TEXT NOT NULL,
                    output TEXT,
                    video_upload_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_productions_updated
                    ON productions(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_productions_upload
                    ON productions(video_upload_id);

                CREATE TABLE IF NOT EXISTS production_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    production_id TEXT NOT NULL REFERENCES productions(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_production_events
                    ON production_events(production_id, id);
                """
            )
            self._ensure_column(connection, "conversations", "user_id", "TEXT")
            self._ensure_column(connection, "video_uploads", "user_id", "TEXT")
            self._ensure_column(connection, "productions", "user_id", "TEXT")
            self._ensure_column(connection, "users", "token_version", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "productions", "replace_policy", "TEXT")
            self._ensure_column(connection, "productions", "replace_plan", "TEXT")
            self._ensure_column(
                connection,
                "productions",
                "conversation_id",
                "TEXT REFERENCES conversations(id) ON DELETE SET NULL",
            )
            self._ensure_column(connection, "productions", "source_item_id", "TEXT")
            self._ensure_column(connection, "productions", "landing_page", "TEXT")
            self._ensure_column(connection, "productions", "script_candidates", "TEXT")
            self._ensure_column(connection, "productions", "selected_script_id", "TEXT")
            self._ensure_column(connection, "productions", "audio_separation", "TEXT")
            self._ensure_column(connection, "productions", "subtitle_style", "TEXT")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_productions_conversation ON productions(conversation_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversations_user_updated ON conversations(user_id, updated_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_productions_user_updated ON productions(user_id, updated_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_uploads_user_created ON video_uploads(user_id, created_at DESC)"
            )

    def _ensure_column(self, connection: sqlite3.Connection, table: str, name: str, decl: str) -> None:
        existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        if name not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    def list_users(self) -> list[dict[str, Any]]:
        rows = self.query(
            """
            SELECT id, username, role, display_name, sso_user_id, area_id, created_at, updated_at
            FROM users
            ORDER BY created_at ASC
            """
        )
        return [dict(row) for row in rows]

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        row = self.query_one(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
            (username.strip(),),
        )
        return dict(row) if row else None

    def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        row = self.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
        return dict(row) if row else None

    def get_user_by_sso_id(self, sso_user_id: int) -> dict[str, Any] | None:
        row = self.query_one("SELECT * FROM users WHERE sso_user_id = ?", (sso_user_id,))
        return dict(row) if row else None

    def create_user(
        self,
        *,
        username: str,
        password_hash: str,
        role: str = "user",
        display_name: str = "",
        sso_user_id: int | None = None,
        area_id: int | None = None,
    ) -> dict[str, Any]:
        user_id = uuid.uuid4().hex
        now = utc_now()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO users (
                    id, username, password_hash, role, display_name,
                    sso_user_id, area_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    username.strip(),
                    password_hash,
                    role,
                    display_name.strip(),
                    sso_user_id,
                    area_id,
                    now,
                    now,
                ),
            )
        return self.get_user_by_id(user_id) or {}

    def update_user(
        self,
        user_id: str,
        *,
        username: str | None = None,
        password_hash: str | None = None,
        role: str | None = None,
        display_name: str | None = None,
        sso_user_id: int | None = None,
        area_id: int | None = None,
        revoke_tokens: bool = False,
    ) -> dict[str, Any] | None:
        updates: list[str] = []
        values: list[Any] = []
        fields = {
            "username": username,
            "password_hash": password_hash,
            "role": role,
            "display_name": display_name,
            "sso_user_id": sso_user_id,
            "area_id": area_id,
        }
        for key, value in fields.items():
            if value is None:
                continue
            updates.append(f"{key} = ?")
            values.append(value.strip() if key == "username" and isinstance(value, str) else value)
        if revoke_tokens:
            updates.append("token_version = token_version + 1")
        if not updates:
            return self.get_user_by_id(user_id)
        updates.append("updated_at = ?")
        values.append(utc_now())
        values.append(user_id)
        with self.transaction() as connection:
            connection.execute(
                f"UPDATE users SET {', '.join(updates)} WHERE id = ?",
                tuple(values),
            )
        return self.get_user_by_id(user_id)

    def delete_user(self, user_id: str) -> None:
        with self.transaction() as connection:
            connection.execute("DELETE FROM users WHERE id = ?", (user_id,))

    def assign_legacy_ownership(self, user_id: str) -> None:
        """Assign rows created before authentication was enabled to the bootstrap admin."""
        with self.transaction() as connection:
            for table in ("conversations", "video_uploads", "productions"):
                connection.execute(
                    f"UPDATE {table} SET user_id = ? WHERE user_id IS NULL OR user_id = ''",
                    (user_id,),
                )

    def get_user_settings(self, user_id: str) -> dict[str, Any] | None:
        row = self.query_one("SELECT * FROM user_settings WHERE user_id = ?", (user_id,))
        if not row:
            return None
        return {
            "public": _load(row["public_json"], {}),
            "secrets": _load(row["secrets_json"], {}),
            "updated_at": row["updated_at"],
        }

    def upsert_user_settings(
        self,
        user_id: str,
        public: dict[str, Any],
        secrets: dict[str, Any],
    ) -> None:
        now = utc_now()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO user_settings (user_id, public_json, secrets_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    public_json = excluded.public_json,
                    secrets_json = excluded.secrets_json,
                    updated_at = excluded.updated_at
                """,
                (user_id, _json(public), _json(secrets), now),
            )

    def adopt_legacy_app_settings(self, user_id: str) -> bool:
        if self.get_user_settings(user_id) is not None:
            return False
        row = self.get_app_settings()
        if not row:
            return False
        self.upsert_user_settings(
            user_id,
            row.get("public") or {},
            row.get("secrets") or {},
        )
        return True

    def create_conversation(self, payload: dict[str, Any]) -> dict[str, Any]:
        conversation_id = uuid.uuid4().hex
        now = utc_now()
        values = {
            "id": conversation_id,
            "user_id": payload.get("user_id"),
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
                    id, user_id, title, status, cwd, model, reasoning_effort, sandbox_mode,
                    approval_policy, developer_instructions, codex_thread_id,
                    active_turn_id, turn_generation, last_error, archived_at,
                    created_at, updated_at
                ) VALUES (
                    :id, :user_id, :title, :status, :cwd, :model, :reasoning_effort, :sandbox_mode,
                    :approval_policy, :developer_instructions, :codex_thread_id,
                    :active_turn_id, :turn_generation, :last_error, :archived_at,
                    :created_at, :updated_at
                )
                """,
                values,
            )
        return self.get_conversation(conversation_id) or {}

    def get_conversation(
        self,
        conversation_id: str,
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        if user_id is None:
            row = self.query_one("SELECT * FROM conversations WHERE id = ?", (conversation_id,))
        else:
            row = self.query_one(
                "SELECT * FROM conversations WHERE id = ? AND user_id = ?",
                (conversation_id, user_id),
            )
        return dict(row) if row else None

    def list_conversations(
        self,
        include_archived: bool = False,
        user_id: str | None = None,
        *,
        archived: bool | None = None,
        limit: int = 30,
        cursor: str | None = None,
        query: str | None = None,
    ) -> dict[str, Any]:
        conditions: list[str] = []
        values: list[Any] = []
        if user_id is not None:
            conditions.append("c.user_id = ?")
            values.append(user_id)
        if archived is True:
            conditions.append("c.archived_at IS NOT NULL")
        elif archived is False or not include_archived:
            conditions.append("c.archived_at IS NULL")
        if query and query.strip():
            needle = query.strip()
            conditions.append(
                """(
                    instr(lower(c.title), lower(?)) > 0
                    OR instr(lower(COALESCE(c.last_error, '')), lower(?)) > 0
                )"""
            )
            values.extend((needle, needle))
        if cursor:
            cursor_updated_at, cursor_id = decode_conversation_cursor(cursor)
            conditions.append(
                "(c.updated_at < ? OR (c.updated_at = ? AND c.id < ?))"
            )
            values.extend((cursor_updated_at, cursor_updated_at, cursor_id))
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        page_size = max(1, min(int(limit), 100))
        rows = self.query(
            f"""
            WITH summaries AS (
                SELECT
                    c.id,
                    c.title,
                    c.status,
                    c.model,
                    c.reasoning_effort,
                    c.last_error,
                    c.archived_at,
                    c.created_at,
                    c.updated_at,
                    CASE WHEN EXISTS (
                        SELECT 1 FROM items i WHERE i.conversation_id = c.id
                    ) THEN 1 ELSE 0 END AS has_items,
                    CASE WHEN EXISTS (
                        SELECT 1
                        FROM items i
                        WHERE i.conversation_id = c.id
                          AND (
                            i.status = 'failed'
                            OR json_extract(i.payload, '$.status') = 'failed'
                            OR json_extract(i.payload, '$.success') = 0
                            OR (
                                json_extract(i.payload, '$.exitCode') IS NOT NULL
                                AND json_extract(i.payload, '$.exitCode') != 0
                            )
                          )
                    ) THEN 1 ELSE 0 END AS has_warnings
                FROM conversations c
                {where}
            )
            SELECT
                *,
                CASE
                    WHEN status = 'failed'
                      OR (last_error IS NOT NULL AND trim(last_error) != '')
                    THEN 'failed'
                    WHEN status = 'running' THEN 'running'
                    WHEN has_warnings = 1 THEN 'warning'
                    WHEN has_items = 1 THEN 'completed'
                    ELSE 'empty'
                END AS list_state
            FROM summaries
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (*values, page_size + 1),
        )
        has_more = len(rows) > page_size
        items = [dict(row) for row in rows[:page_size]]
        for item in items:
            item["has_warnings"] = bool(item.get("has_warnings"))
        next_cursor = (
            encode_conversation_cursor(items[-1]["updated_at"], items[-1]["id"])
            if has_more and items
            else None
        )
        return {"items": items, "next_cursor": next_cursor}

    def update_conversation(
        self,
        conversation_id: str,
        fields: dict[str, Any],
        user_id: str | None = None,
    ) -> None:
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
        owner_clause = " AND user_id = ?" if user_id is not None else ""
        params = (*values.values(), conversation_id)
        if user_id is not None:
            params = (*params, user_id)
        with self.transaction() as connection:
            connection.execute(
                f"UPDATE conversations SET {assignments} WHERE id = ?{owner_clause}",
                params,
            )

    def delete_conversation(self, conversation_id: str, user_id: str | None = None) -> None:
        owner_clause = " AND user_id = ?" if user_id is not None else ""
        params = (conversation_id,) if user_id is None else (conversation_id, user_id)
        with self.transaction() as connection:
            connection.execute(
                f"DELETE FROM conversations WHERE id = ?{owner_clause}",
                params,
            )

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

    def list_events(
        self,
        conversation_id: str,
        after_id: int = 0,
        limit: int = 300,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if user_id is None:
            rows = self.query(
                """
                SELECT * FROM events
                WHERE conversation_id = ? AND id > ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (conversation_id, after_id, limit),
            )
        else:
            rows = self.query(
                """
                SELECT e.* FROM events e
                JOIN conversations c ON c.id = e.conversation_id
                WHERE e.conversation_id = ? AND e.id > ? AND c.user_id = ?
                ORDER BY e.id ASC
                LIMIT ?
                """,
                (conversation_id, after_id, user_id, limit),
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

    def latest_event_id(self, conversation_id: str) -> int:
        row = self.query_one(
            "SELECT COALESCE(MAX(id), 0) AS event_id FROM events WHERE conversation_id = ?",
            (conversation_id,),
        )
        return int(row["event_id"]) if row else 0

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

    def delete_item(self, item_id: str, conversation_id: str | None = None) -> bool:
        with self.transaction() as connection:
            if conversation_id is None:
                cursor = connection.execute("DELETE FROM items WHERE id = ?", (item_id,))
            else:
                cursor = connection.execute(
                    "DELETE FROM items WHERE id = ? AND conversation_id = ?",
                    (item_id, conversation_id),
                )
            return cursor.rowcount > 0

    def upsert_item(self, conversation_id: str, item_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        existing = self.get_item(item_id)
        now = utc_now()
        if existing:
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
                        _json(fields.get("payload") if "payload" in fields else existing.get("payload") or {}),
                        now,
                        existing["id"],
                    ),
                )
                connection.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?",
                    (now, conversation_id),
                )
            return self.get_item(existing["id"]) or {}
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

    def recover_orphaned_runs(self, reason: str) -> list[str]:
        now = utc_now()
        with self.transaction() as connection:
            rows = connection.execute("SELECT id FROM conversations WHERE status = 'running'").fetchall()
            conversation_ids = [str(row["id"]) for row in rows]
            connection.execute(
                """
                UPDATE approvals
                SET decision = 'cancel', reason = ?, resolved_at = ?
                WHERE decision IS NULL
                """,
                (reason, now),
            )
            connection.execute(
                """
                UPDATE clarifications
                SET answers = '{}', resolved_at = ?
                WHERE answers IS NULL
                """,
                (now,),
            )
            connection.execute(
                """
                UPDATE conversations
                SET status = 'failed', active_turn_id = NULL, last_error = ?, updated_at = ?
                WHERE status = 'running'
                """,
                (reason, now),
            )
        return conversation_ids

    def attachment_reference_count(self, path: str) -> int:
        row = self.query_one(
            "SELECT COUNT(*) AS count FROM items WHERE payload LIKE ?",
            (f"%{path}%",),
        )
        return int(row["count"]) if row else 0

    def get_app_settings(self) -> dict[str, Any] | None:
        row = self.query_one("SELECT * FROM app_settings WHERE id = 1")
        if not row:
            return None
        return {
            "public": _load(row["public_json"], {}),
            "secrets": _load(row["secrets_json"], {}),
            "updated_at": row["updated_at"],
        }

    def upsert_app_settings(self, public: dict[str, Any], secrets: dict[str, Any]) -> None:
        now = utc_now()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO app_settings (id, public_json, secrets_json, updated_at)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    public_json = excluded.public_json,
                    secrets_json = excluded.secrets_json,
                    updated_at = excluded.updated_at
                """,
                (_json(public), _json(secrets), now),
            )

    def create_video_upload(self, payload: dict[str, Any]) -> dict[str, Any]:
        values = {
            "id": payload["id"],
            "user_id": payload.get("user_id"),
            "name": payload["name"],
            "path": payload["path"],
            "size": int(payload["size"]),
            "content_type": payload["content_type"],
            "duration_ms": int(payload["duration_ms"]),
            "width": int(payload["width"]),
            "height": int(payload["height"]),
            "fps": float(payload["fps"]),
            "created_at": payload.get("created_at") or utc_now(),
        }
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO video_uploads (
                    id, user_id, name, path, size, content_type, duration_ms, width, height, fps, created_at
                ) VALUES (
                    :id, :user_id, :name, :path, :size, :content_type, :duration_ms, :width, :height, :fps, :created_at
                )
                """,
                values,
            )
        return self.get_video_upload(values["id"]) or values

    def get_video_upload(self, upload_id: str, user_id: str | None = None) -> dict[str, Any] | None:
        if user_id is None:
            row = self.query_one("SELECT * FROM video_uploads WHERE id = ?", (upload_id,))
        else:
            row = self.query_one(
                "SELECT * FROM video_uploads WHERE id = ? AND user_id = ?",
                (upload_id, user_id),
            )
        return dict(row) if row else None

    def count_productions_for_upload(self, upload_id: str) -> int:
        row = self.query_one(
            "SELECT COUNT(*) AS count FROM productions WHERE video_upload_id = ?",
            (upload_id,),
        )
        return int(row["count"]) if row else 0

    def delete_video_upload(self, upload_id: str) -> None:
        with self.transaction() as connection:
            connection.execute("DELETE FROM video_uploads WHERE id = ?", (upload_id,))

    def create_production(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        values = {
            "id": payload["id"],
            "user_id": payload.get("user_id"),
            "title": payload.get("title") or "",
            "brief": payload["brief"],
            "status": payload.get("status") or "draft",
            "current_stage": payload.get("current_stage") or "ingest",
            "error": payload.get("error"),
            "warnings": _json(payload.get("warnings") or []),
            "source_video": _json(payload.get("source_video") or {}),
            "decomposition": _json(payload.get("decomposition")),
            "intent": _json(payload.get("intent")),
            "skeleton": _json(payload.get("skeleton")),
            "script": _json(payload.get("script")),
            "clips": _json(payload.get("clips") or []),
            "output": _json(payload.get("output")),
            "replace_policy": json.dumps(
                payload.get("replace_policy") or {"mode": "default", "roles": [], "segment_ids": [], "time_ranges": []},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "replace_plan": json.dumps(payload.get("replace_plan"), ensure_ascii=False, separators=(",", ":")),
            "video_upload_id": payload["video_upload_id"],
            "conversation_id": payload.get("conversation_id"),
            "source_item_id": payload.get("source_item_id"),
            "created_at": now,
            "updated_at": now,
        }
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO productions (
                    id, user_id, title, brief, status, current_stage, error, warnings, source_video,
                    decomposition, intent, skeleton, script, clips, output, replace_policy,
                    replace_plan, video_upload_id, conversation_id, source_item_id, created_at, updated_at
                ) VALUES (
                    :id, :user_id, :title, :brief, :status, :current_stage, :error, :warnings, :source_video,
                    :decomposition, :intent, :skeleton, :script, :clips, :output, :replace_policy,
                    :replace_plan, :video_upload_id, :conversation_id, :source_item_id, :created_at, :updated_at
                )
                """,
                values,
            )
        return self.get_production(values["id"]) or {}

    def get_production(
        self,
        production_id: str,
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        if user_id is None:
            row = self.query_one("SELECT * FROM productions WHERE id = ?", (production_id,))
        else:
            row = self.query_one(
                "SELECT * FROM productions WHERE id = ? AND user_id = ?",
                (production_id, user_id),
            )
        return self._production_from_row(row) if row else None

    def list_productions(
        self,
        conversation_id: str | None = None,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        conditions: list[str] = []
        values: list[Any] = []
        if conversation_id:
            conditions.append("conversation_id = ?")
            values.append(conversation_id)
        if user_id is not None:
            conditions.append("user_id = ?")
            values.append(user_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        if conditions:
            rows = self.query(
                f"SELECT * FROM productions {where} ORDER BY created_at DESC, id DESC",
                tuple(values),
            )
        else:
            rows = self.query("SELECT * FROM productions ORDER BY created_at DESC, id DESC")
        return [self._production_from_row(row) for row in rows]

    def find_production_by_source_item(
        self,
        source_item_id: str,
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        if not source_item_id:
            return None
        if user_id is None:
            row = self.query_one(
                "SELECT * FROM productions WHERE source_item_id = ? ORDER BY created_at DESC, id DESC",
                (source_item_id,),
            )
        else:
            row = self.query_one(
                """
                SELECT * FROM productions
                WHERE source_item_id = ? AND user_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (source_item_id, user_id),
            )
        return self._production_from_row(row) if row else None

    def find_duplicate_production(
        self,
        conversation_id: str,
        video_upload_id: str,
        brief: str,
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        if not conversation_id or not video_upload_id:
            return None
        conditions = "conversation_id = ? AND video_upload_id = ? AND brief = ?"
        values: tuple[Any, ...] = (conversation_id, video_upload_id, brief)
        if user_id is not None:
            conditions += " AND user_id = ?"
            values = (*values, user_id)
        row = self.query_one(
            f"""
            SELECT * FROM productions
            WHERE {conditions}
            ORDER BY created_at DESC, id DESC
            """,
            values,
        )
        return self._production_from_row(row) if row else None

    def update_production(self, production_id: str, fields: dict[str, Any]) -> None:
        allowed = {
            "title",
            "brief",
            "status",
            "current_stage",
            "error",
            "warnings",
            "source_video",
            "decomposition",
            "intent",
            "skeleton",
            "script",
            "clips",
            "output",
            "replace_policy",
            "replace_plan",
            "conversation_id",
            "source_item_id",
            "landing_page",
            "script_candidates",
            "selected_script_id",
            "audio_separation",
            "subtitle_style",
        }
        json_fields = {
            "warnings",
            "source_video",
            "decomposition",
            "intent",
            "skeleton",
            "script",
            "clips",
            "output",
            "replace_policy",
            "replace_plan",
            "landing_page",
            "script_candidates",
            "audio_separation",
            "subtitle_style",
        }
        values: dict[str, Any] = {}
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key in {"replace_policy", "replace_plan"}:
                values[key] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            elif key in json_fields:
                values[key] = _json(value)
            else:
                values[key] = value
        values["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self.transaction() as connection:
            connection.execute(
                f"UPDATE productions SET {assignments} WHERE id = ?",
                (*values.values(), production_id),
            )

    def delete_production(self, production_id: str) -> None:
        with self.transaction() as connection:
            connection.execute("DELETE FROM productions WHERE id = ?", (production_id,))

    def append_production_event(self, production_id: str, kind: str, payload: Any) -> int:
        now = utc_now()
        with self.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO production_events (production_id, kind, payload, created_at) VALUES (?, ?, ?, ?)",
                (production_id, kind, _json(payload), now),
            )
            connection.execute(
                "UPDATE productions SET updated_at = ? WHERE id = ?",
                (now, production_id),
            )
            return int(cursor.lastrowid)

    def list_production_events(
        self,
        production_id: str,
        after_id: int = 0,
        limit: int = 300,
        user_id: str | None = None,
        newest_first: bool = False,
    ) -> list[dict[str, Any]]:
        order = "DESC" if newest_first else "ASC"
        if user_id is None:
            rows = self.query(
                f"""
                SELECT * FROM production_events
                WHERE production_id = ? AND id > ?
                ORDER BY id {order}
                LIMIT ?
                """,
                (production_id, after_id, limit),
            )
        else:
            rows = self.query(
                f"""
                SELECT e.* FROM production_events e
                JOIN productions p ON p.id = e.production_id
                WHERE e.production_id = ? AND e.id > ? AND p.user_id = ?
                ORDER BY e.id {order}
                LIMIT ?
                """,
                (production_id, after_id, user_id, limit),
            )
        return [
            {
                "id": row["id"],
                "type": row["kind"],
                "payload": _load(row["payload"], {}),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def _production_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        production_id = row["id"]
        output = _load(row["output"], None)
        return {
            "id": production_id,
            "user_id": _row_get(row, "user_id"),
            "title": row["title"],
            "brief": row["brief"],
            "status": row["status"],
            "current_stage": row["current_stage"],
            "error": row["error"],
            "warnings": _load(row["warnings"], []),
            "source_video": _load(row["source_video"], {}),
            "decomposition": _load(row["decomposition"], None),
            "intent": _load(row["intent"], None),
            "skeleton": _load(row["skeleton"], None),
            "script": _load(row["script"], None),
            "clips": _load(row["clips"], []),
            "output": output,
            "replace_policy": _load(
                _row_get(row, "replace_policy"),
                {"mode": "default", "roles": [], "segment_ids": [], "time_ranges": []},
            ),
            "replace_plan": _load(_row_get(row, "replace_plan"), None),
            "video_upload_id": row["video_upload_id"],
            "conversation_id": _row_get(row, "conversation_id"),
            "source_item_id": _row_get(row, "source_item_id"),
            "landing_page": _load(_row_get(row, "landing_page"), None),
            "script_candidates": _load(_row_get(row, "script_candidates"), []),
            "selected_script_id": _row_get(row, "selected_script_id"),
            "audio_separation": _load(_row_get(row, "audio_separation"), None),
            "subtitle_style": _load(_row_get(row, "subtitle_style"), None),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
