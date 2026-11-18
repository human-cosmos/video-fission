from __future__ import annotations

import hashlib
from pathlib import Path


def user_segment(user_id: str | None) -> str:
    """Return a stable, filesystem-safe directory name for a user id."""
    value = str(user_id or "").strip()
    if not value:
        return ""
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"u_{digest}"


def user_workspace(root: Path, user_id: str | None) -> Path:
    segment = user_segment(user_id)
    return root / "users" / segment if segment else root


def user_upload_root(root: Path, user_id: str | None, *, production: bool = False) -> Path:
    base = user_workspace(root, user_id)
    return base / "productions" if production else base


def user_artifact_root(root: Path, user_id: str | None) -> Path:
    return user_workspace(root, user_id)


def conversation_artifact_dir(root: Path, user_id: str | None, conversation_id: str) -> Path:
    if user_id:
        return user_artifact_root(root, user_id) / "conversations" / conversation_id
    return root / conversation_id


def production_artifact_dir(root: Path, user_id: str | None, production_id: str) -> Path:
    if user_id:
        return user_artifact_root(root, user_id) / "productions" / production_id
    return root / "productions" / production_id
