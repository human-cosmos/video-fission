#!/usr/bin/env python3
"""Inspect the newest production task and its execution logs."""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = PROJECT_ROOT / "status" / "video-fission.log"
DB_PATH = PROJECT_ROOT / "data" / "video_fission.db"
BACKEND_HEALTH_URL = "http://127.0.0.1:8000/api/health"
FRONTEND_URL = "http://127.0.0.1:5174/"
LOCAL_TZ = timezone(timedelta(hours=8))
LOG_TAIL_BYTES = 5 * 1024 * 1024
RECENT_WINDOW_SECONDS = 30
TASK_RELEVANT_MODULES = (
    "video_fission.app_server",
    "video_fission.media",
    "video_fission.pipeline",
    "video_fission.productions",
    "video_fission.providers",
    "video_fission.understand",
)
LOG_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s+"
    r"(?P<level>[A-Z]+)\s+\[(?P<context>[^\]]*)\]\s+"
    r"(?P<module>[^:]+):\s+(?P<message>.*)$"
)


@dataclass(frozen=True)
class LogEntry:
    timestamp: datetime
    level: str
    context: str
    module: str
    message: str
    raw: str


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(LOCAL_TZ)


def format_dt(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else "-"


def age_seconds(value: datetime | None, now: datetime) -> int | None:
    if value is None:
        return None
    return max(0, int((now - value).total_seconds()))


def fetch(url: str, timeout: float = 5.0) -> dict[str, Any]:
    started = datetime.now(LOCAL_TZ)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read()
            return {
                "ok": 200 <= response.status < 400,
                "status": response.status,
                "elapsed_ms": round(
                    (datetime.now(LOCAL_TZ) - started).total_seconds() * 1000, 1
                ),
                "body": body.decode("utf-8", errors="replace"),
            }
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {
            "ok": False,
            "status": None,
            "elapsed_ms": round(
                (datetime.now(LOCAL_TZ) - started).total_seconds() * 1000, 1
            ),
            "error": f"{exc.__class__.__name__}: {exc}",
        }


def read_latest_task() -> dict[str, Any] | None:
    if not DB_PATH.exists():
        return None
    connection = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """
            select id, status, current_stage, error, created_at, updated_at,
                   conversation_id, title
            from productions
            order by created_at desc
            limit 1
            """
        ).fetchone()
        if row is None:
            return None
        task = dict(row)
        events = connection.execute(
            """
            select id, kind, payload, created_at
            from production_events
            where production_id = ?
            order by id desc
            limit 40
            """,
            (task["id"],),
        ).fetchall()
        task["recent_events"] = [dict(event) for event in events]
        return task
    finally:
        connection.close()


def parse_log_line(raw: str) -> LogEntry | None:
    match = LOG_RE.match(raw.rstrip("\r\n"))
    if not match:
        return None
    try:
        timestamp = datetime.strptime(
            match.group("timestamp"), "%Y-%m-%d %H:%M:%S,%f"
        ).replace(tzinfo=LOCAL_TZ)
    except ValueError:
        return None
    return LogEntry(
        timestamp=timestamp,
        level=match.group("level"),
        context=match.group("context"),
        module=match.group("module").strip(),
        message=match.group("message"),
        raw=raw.rstrip("\r\n"),
    )


def is_task_log(entry: LogEntry, task_id: str, task_started: datetime) -> bool:
    if entry.timestamp < task_started:
        return False
    if f"prd={task_id}" in entry.context:
        return True
    if f"production-{task_id}" in entry.context:
        return True
    if not entry.module.startswith(TASK_RELEVANT_MODULES):
        return False
    return any(
        marker in entry.context
        for marker in (
            "codex-request-item/tool/call",
            "video-understand-request",
            f"production-{task_id}",
        )
    )


def read_task_logs(task_id: str, task_started: datetime) -> list[LogEntry]:
    if not LOG_PATH.exists():
        return []
    entries: list[LogEntry] = []
    with LOG_PATH.open("rb") as handle:
        size = LOG_PATH.stat().st_size
        handle.seek(max(0, size - LOG_TAIL_BYTES))
        if handle.tell() > 0:
            handle.readline()
        for raw_bytes in handle:
            raw = raw_bytes.decode("utf-8", errors="replace")
            parsed = parse_log_line(raw)
            if parsed and is_task_log(parsed, task_id, task_started):
                entries.append(parsed)
    return entries


def parse_payload(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


def summarize_event(event: dict[str, Any]) -> dict[str, Any]:
    payload = parse_payload(event.get("payload"))
    if isinstance(payload, dict):
        payload = {
            key: payload[key]
            for key in (
                "stage",
                "step",
                "message",
                "tool",
                "success",
                "details",
                "error",
            )
            if key in payload
        }
    return {
        "id": event.get("id"),
        "kind": event.get("kind"),
        "at": format_dt(parse_iso(event.get("created_at"))),
        "payload": payload,
    }


def normalize_message(message: str) -> str:
    message = re.sub(r"\b(?:prd|req|upl|conv|seg)_[A-Za-z0-9]+\b", "<id>", message)
    message = re.sub(r"\b\d+(?:\.\d+)?(?:ms|s|B)?\b", "<n>", message)
    return message[:500]


def issue_signature(entry: LogEntry) -> str:
    return f"{entry.module}: {normalize_message(entry.message)}"


def latest_operation(
    entries: list[LogEntry], start_marker: str, end_markers: tuple[str, ...]
) -> dict[str, Any] | None:
    start_index = None
    for index in range(len(entries) - 1, -1, -1):
        if start_marker in entries[index].message:
            start_index = index
            break
    if start_index is None:
        return None
    start = entries[start_index]
    completed = next(
        (
            entry
            for entry in entries[start_index + 1 :]
            if any(marker in entry.message for marker in end_markers)
        ),
        None,
    )
    timeout_match = re.search(r"timeout_s=([\d.]+)", start.message)
    timeout = float(timeout_match.group(1)) if timeout_match else None
    return {
        "name": start_marker,
        "started_at": start.timestamp,
        "completed_at": completed.timestamp if completed else None,
        "status": "completed" if completed else "running",
        "timeout_s": timeout,
        "evidence": start.message[:700],
        "completion_evidence": completed.message[:500] if completed else None,
    }


def operation_report(
    operation: dict[str, Any] | None, now: datetime
) -> dict[str, Any] | None:
    if operation is None:
        return None
    elapsed = age_seconds(operation["started_at"], now) or 0
    timeout = operation.get("timeout_s")
    overdue = (
        operation["status"] == "running"
        and timeout is not None
        and elapsed > timeout
    )
    return {
        "name": operation["name"],
        "status": "overdue" if overdue else operation["status"],
        "started_at": format_dt(operation["started_at"]),
        "elapsed_s": elapsed,
        "timeout_s": timeout,
        "evidence": operation["evidence"],
        "completion_evidence": operation["completion_evidence"],
    }


def main() -> int:
    now = datetime.now(LOCAL_TZ)
    task = read_latest_task()
    service_health = fetch(BACKEND_HEALTH_URL)
    frontend_health = fetch(FRONTEND_URL)

    health_body: dict[str, Any] = {}
    if service_health.get("body"):
        try:
            health_body = json.loads(service_health["body"])
        except json.JSONDecodeError:
            health_body = {"parse_error": True}

    if task is None:
        print(
            json.dumps(
                {
                    "checked_at": format_dt(now),
                    "assessment": "critical",
                    "summary": "No production task found.",
                    "service": {
                        "backend": service_health,
                        "frontend": frontend_health,
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    task_started = parse_iso(task.get("created_at"))
    if task_started is None:
        task_started = now - timedelta(hours=1)
    task_updated = parse_iso(task.get("updated_at"))
    logs = read_task_logs(task["id"], task_started)
    recent_logs = [
        entry
        for entry in logs
        if (now - entry.timestamp).total_seconds() <= RECENT_WINDOW_SECONDS
    ]

    recent_events = [
        event
        for event in task.get("recent_events", [])
        if (now - (parse_iso(event.get("created_at")) or now)).total_seconds()
        <= RECENT_WINDOW_SECONDS
    ]

    levels = Counter(entry.level for entry in logs)
    recent_levels = Counter(entry.level for entry in recent_logs)
    issue_groups: dict[str, dict[str, Any]] = {}
    for entry in recent_logs:
        if entry.level not in {"WARNING", "ERROR", "CRITICAL"}:
            continue
        signature = issue_signature(entry)
        group = issue_groups.setdefault(
            signature,
            {
                "severity": entry.level,
                "module": entry.module,
                "message": entry.message[:500],
                "count": 0,
                "first_at": entry.timestamp,
                "last_at": entry.timestamp,
            },
        )
        group["count"] += 1
        group["first_at"] = min(group["first_at"], entry.timestamp)
        group["last_at"] = max(group["last_at"], entry.timestamp)

    issues = []
    for group in issue_groups.values():
        issues.append(
            {
                "severity": group["severity"],
                "module": group["module"],
                "message": group["message"],
                "count": group["count"],
                "first_at": format_dt(group["first_at"]),
                "last_at": format_dt(group["last_at"]),
            }
        )
    issues.sort(key=lambda item: (item["severity"] != "ERROR", item["first_at"]))

    operations = [
        operation_report(
            latest_operation(
                logs,
                "gemini_gateway.upload_started",
                ("gemini_gateway.upload_completed", "gemini_gateway.upload_failed"),
            ),
            now,
        ),
        operation_report(
            latest_operation(
                logs,
                "llm.understand_call_started",
                (
                    "llm.understand_call_completed",
                    "llm.understand_call_failed",
                    "understand_call_completed",
                    "understand_call_failed",
                ),
            ),
            now,
        ),
    ]
    operations = [item for item in operations if item is not None]

    latest_log = logs[-1] if logs else None
    last_activity = latest_log.timestamp if latest_log else task_updated
    inactive_for = age_seconds(last_activity, now)
    active_task = task.get("status") in {"queued", "pending", "running", "preparing"}
    overdue_operations = [
        operation for operation in operations if operation["status"] == "overdue"
    ]
    recent_errors = [
        issue for issue in issues if issue["severity"] in {"ERROR", "CRITICAL"}
    ]

    if not service_health.get("ok") or task.get("status") == "failed":
        assessment = "critical"
    elif recent_errors or overdue_operations:
        assessment = "critical"
    elif issues or (active_task and inactive_for and inactive_for > 120):
        assessment = "degraded"
    else:
        assessment = "healthy"

    if latest_log:
        latest_line = f"{latest_log.module}: {latest_log.message}"
    else:
        latest_line = None

    report = {
        "checked_at": format_dt(now),
        "assessment": assessment,
        "task": {
            "id": task["id"],
            "title": task.get("title"),
            "status": task.get("status"),
            "current_stage": task.get("current_stage"),
            "created_at": format_dt(task_started),
            "updated_at": format_dt(task_updated),
            "updated_age_s": age_seconds(task_updated, now),
            "error": task.get("error"),
        },
        "service": {
            "backend": {
                "ok": service_health.get("ok"),
                "status": service_health.get("status"),
                "elapsed_ms": service_health.get("elapsed_ms"),
                "error": service_health.get("error"),
            },
            "runtime": health_body.get("runtime"),
            "ffmpeg": health_body.get("ffmpeg"),
            "pipeline": health_body.get("pipeline"),
            "logging": health_body.get("logging"),
            "frontend": {
                "ok": frontend_health.get("ok"),
                "status": frontend_health.get("status"),
                "elapsed_ms": frontend_health.get("elapsed_ms"),
                "error": frontend_health.get("error"),
            },
        },
        "task_log_window": {
            "window_s": RECENT_WINDOW_SECONDS,
            "lines": len(recent_logs),
            "levels": dict(recent_levels),
            "latest_at": format_dt(latest_log.timestamp) if latest_log else None,
            "latest_age_s": inactive_for,
            "latest_line": latest_line[:1000] if latest_line else None,
        },
        "task_log_totals": {
            "lines": len(logs),
            "levels": dict(levels),
        },
        "active_operations": operations,
        "new_issues": issues,
        "new_production_events": [
            summarize_event(event) for event in recent_events[:10]
        ],
        "latest_production_events": [
            summarize_event(event) for event in task.get("recent_events", [])[:8]
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if assessment != "critical" else 2


if __name__ == "__main__":
    sys.exit(main())
