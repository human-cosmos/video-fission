from __future__ import annotations

import contextvars
import logging
import os
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

_CONTEXT_FIELDS = (
    "request_id",
    "conversation_id",
    "production_id",
    "stage",
    "step",
    "provider",
)
_CONTEXT_VARS = {
    field: contextvars.ContextVar(f"video_fission_log_{field}", default="-")
    for field in _CONTEXT_FIELDS
}
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ACTIVE_LOG_PATH: Path | None = None
_LOG_CONFIGURATION_ERROR = ""


def _env_int(name: str, default: int, minimum: int) -> int:
    try:
        return max(int(os.getenv(name, str(default))), minimum)
    except (TypeError, ValueError):
        return default


def _safe_log_text(value: Any, limit: int | None = 300) -> str:
    text = str(value or "")
    cleaned: list[str] = []
    for character in text:
        code = ord(character)
        if character == "\r":
            cleaned.append("\\r")
        elif character == "\n":
            cleaned.append("\\n")
        elif character == "\t":
            cleaned.append("\\t")
        elif code < 32 or code == 127:
            cleaned.append(f"\\x{code:02x}")
        elif character.isprintable():
            cleaned.append(character)
        else:
            cleaned.append("?")
    text = "".join(cleaned)
    return text if limit is None else text[: max(limit, 0)]


def safe_request_id(value: Any) -> str:
    text = str(value or "").strip()
    return text if _REQUEST_ID_RE.fullmatch(text) else ""


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for field, variable in _CONTEXT_VARS.items():
            setattr(record, field, variable.get() or "-")
        return True


def _is_running_tests() -> bool:
    return (
        os.getenv("PYTEST_CURRENT_TEST") is not None
        or os.getenv("PYTEST_VERSION") is not None
        or "pytest" in sys.modules
        or "_pytest" in sys.modules
    )


def logging_status() -> dict[str, Any]:
    handler = next(
        (item for item in logging.getLogger().handlers if item.get_name() == "video-fission-file"),
        None,
    )
    path = _ACTIVE_LOG_PATH
    return {
        "enabled": handler is not None and path is not None,
        "path": str(path) if path else "",
        "error": _LOG_CONFIGURATION_ERROR,
    }


def configure_logging(project_root: Path, *, force_file: bool = False) -> Path | None:
    global _ACTIVE_LOG_PATH, _LOG_CONFIGURATION_ERROR

    level_name = os.getenv("VF_LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s "
        "[req=%(request_id)s conv=%(conversation_id)s prd=%(production_id)s "
        "stage=%(stage)s step=%(step)s provider=%(provider)s thread=%(threadName)s] "
        "%(name)s: %(message)s"
    )
    context_filter = ContextFilter()

    console = next(
        (handler for handler in root.handlers if handler.get_name() == "video-fission-console"),
        None,
    )
    if console is None:
        console = logging.StreamHandler()
        console.set_name("video-fission-console")
        root.addHandler(console)
    console.setLevel(level)
    console.setFormatter(formatter)
    console.addFilter(context_filter)

    configured_from_env = os.getenv("VF_LOG_FILE")
    if configured_from_env is not None:
        # An explicit path is an operator decision and must override pytest
        # process heuristics inherited by long-running server children.
        configured = configured_from_env.strip()
    elif force_file:
        configured = "status/video-fission.log"
    else:
        configured = "0" if _is_running_tests() else "status/video-fission.log"
    if configured.lower() in {"", "0", "false", "off", "none"}:
        existing = next(
            (handler for handler in root.handlers if handler.get_name() == "video-fission-file"),
            None,
        )
        if existing is not None:
            root.removeHandler(existing)
            existing.close()
        _ACTIVE_LOG_PATH = None
        _LOG_CONFIGURATION_ERROR = ""
        return None
    log_path = Path(configured).expanduser()
    if not log_path.is_absolute():
        log_path = project_root / log_path
    log_path = log_path.resolve()
    file_handler = next(
        (handler for handler in root.handlers if handler.get_name() == "video-fission-file"),
        None,
    )
    if file_handler is not None and Path(file_handler.baseFilename).resolve() != log_path:
        root.removeHandler(file_handler)
        file_handler.close()
        file_handler = None
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if file_handler is None:
            file_handler = RotatingFileHandler(
                log_path,
                maxBytes=_env_int("VF_LOG_MAX_BYTES", 20 * 1024 * 1024, 1024 * 1024),
                backupCount=_env_int("VF_LOG_BACKUP_COUNT", 10, 1),
                encoding="utf-8",
                errors="backslashreplace",
                delay=False,
            )
            file_handler.set_name("video-fission-file")
            root.addHandler(file_handler)
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "logging.file_unavailable path=%s error=%s",
            _safe_log_text(log_path, 240),
            exc.__class__.__name__,
        )
        _ACTIVE_LOG_PATH = None
        _LOG_CONFIGURATION_ERROR = f"{exc.__class__.__name__}: {exc}"
        return None
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    if context_filter not in file_handler.filters:
        file_handler.addFilter(context_filter)
    _ACTIVE_LOG_PATH = log_path
    _LOG_CONFIGURATION_ERROR = ""
    logging.getLogger(__name__).info("logging.file_configured path=%s", log_path)
    return log_path


@contextmanager
def log_context(**values: Any) -> Iterator[None]:
    tokens: list[tuple[contextvars.ContextVar[str], contextvars.Token[str]]] = []
    for field, value in values.items():
        variable = _CONTEXT_VARS.get(field)
        if variable is None:
            continue
        tokens.append((variable, variable.set("-" if value is None else _safe_log_text(value, 200))))
    try:
        yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


def clip_text(value: Any, limit: int = 300) -> str:
    text = " ".join(str(value or "").split())
    text = _safe_log_text(text, None)
    if len(text) <= limit:
        return text
    return f"{text[: max(limit - 3, 0)]}..."


def prompt_preview(text: Any, limit: int | None = None) -> str:
    if os.getenv("VF_LOG_PROMPTS", "0").strip().lower() not in {"1", "true", "yes", "on"}:
        return ""
    max_chars = _env_int("VF_LOG_PROMPT_CHARS", 1200, 120)
    return clip_text(text, limit or max_chars)


def safe_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "-"
    if raw.startswith("data:"):
        return raw.split(",", 1)[0].split(";", 1)[0] + ";base64..."
    parsed = urlsplit(raw)
    if not parsed.scheme or not parsed.netloc:
        return clip_text(raw, 240)
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def file_summary(path: Path | str | None) -> str:
    if not path:
        return "-"
    item = Path(path)
    try:
        size = item.stat().st_size
        return f"{item.name}({size}B)"
    except OSError:
        return item.name


def summarize_messages(messages: list[dict[str, Any]]) -> dict[str, Any]:
    text_chars = 0
    image_parts = 0
    video_parts = 0
    roles: list[str] = []
    for message in messages:
        roles.append(str(message.get("role") or "unknown"))
        content = message.get("content")
        items = content if isinstance(content, list) else [content]
        for item in items:
            if isinstance(item, str):
                text_chars += len(item)
            elif isinstance(item, dict):
                kind = str(item.get("type") or "")
                if kind in {"text", "input_text"}:
                    text_chars += len(str(item.get("text") or ""))
                elif kind in {"image_url", "input_image"}:
                    image_parts += 1
                elif kind == "video_url":
                    video_parts += 1
    return {
        "messages": len(messages),
        "roles": ",".join(roles),
        "text_chars": text_chars,
        "image_parts": image_parts,
        "video_parts": video_parts,
    }


def summarize_value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, dict):
        keys = ",".join(str(key) for key in list(value)[:12])
        return f"dict(keys={keys})"
    if isinstance(value, list):
        return f"list({len(value)})"
    if isinstance(value, str):
        return f"str({len(value)} chars)"
    return type(value).__name__
