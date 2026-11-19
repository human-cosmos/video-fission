from __future__ import annotations

import logging
from pathlib import Path

from video_fission.conversation_cards import progress_payload
from video_fission.logging_utils import (
    ContextFilter,
    clip_text,
    configure_logging,
    log_context,
    logging_status,
    safe_request_id,
)


def test_safe_request_id_rejects_header_injection_and_oversized_values():
    assert safe_request_id("trace-123:worker.4") == "trace-123:worker.4"
    assert safe_request_id("trace\nforged") == ""
    assert safe_request_id("a" * 129) == ""


def test_log_context_escapes_control_characters():
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "message", (), None)
    with log_context(conversation_id="conv\nforged", production_id="prd\t1"):
        ContextFilter().filter(record)
    assert record.conversation_id == "conv\\nforged"
    assert record.production_id == "prd\\t1"


def test_clip_text_removes_terminal_escape_sequences():
    value = clip_text("line one\nline two\x1b[31mred", 80)
    assert "\n" not in value
    assert "\x1b" not in value
    assert "\\x1b" in value


def test_explicit_log_file_overrides_inherited_pytest_detection(tmp_path: Path, monkeypatch):
    log_path = tmp_path / "runtime.log"
    monkeypatch.setenv("PYTEST_VERSION", "inherited")
    monkeypatch.setenv("VF_LOG_FILE", str(log_path))
    try:
        configured = configure_logging(tmp_path)
        logging.getLogger("video_fission.test_logging").warning("runtime failure detail")
        for handler in logging.getLogger().handlers:
            handler.flush()

        assert configured == log_path.resolve()
        assert logging_status()["enabled"] is True
        text = log_path.read_text(encoding="utf-8")
        assert "logging.file_configured" in text
        assert "runtime failure detail" in text
    finally:
        monkeypatch.setenv("VF_LOG_FILE", "0")
        configure_logging(tmp_path)


def test_progress_payload_labels_cancellation_as_cancelled():
    public = {
        "id": "prd_cancelled",
        "status": "cancelled",
        "current_stage": "generate",
        "warnings": [],
    }
    events = [
        {
            "id": 1,
            "type": "stage_started",
            "payload": {"stage": "generate"},
            "created_at": "2026-11-18T10:00:00+00:00",
        },
        {
            "id": 2,
            "type": "error",
            "payload": {"stage": "cancelled", "message": "已取消"},
            "created_at": "2026-11-18T10:00:01+00:00",
        },
    ]
    payload = progress_payload(public, events)
    generate = next(stage for stage in payload["stages"] if stage["id"] == "generate")
    assert generate["state"] == "cancelled"
    assert payload["activity"][-1]["text"] == "任务已取消"
    assert payload["activity"][-1]["tone"] == "warning"
