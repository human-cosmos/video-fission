from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from .config import Settings
from .paths import user_workspace


def production_workspace_dir(settings: Settings, production: dict[str, Any]) -> Path:
    production_id = str(production.get("id") or "").strip()
    if not production_id:
        raise ValueError("\u521b\u4f5c\u4efb\u52a1\u7f3a\u5c11 id")
    return user_workspace(settings.workspace, production.get("user_id")) / "productions" / production_id


def creative_context_paths(settings: Settings, production: dict[str, Any]) -> dict[str, str]:
    folder = production_workspace_dir(settings, production)
    return {
        "workspace_dir": str(folder),
        "context_path": str(folder / "creative-context.json"),
        "timeline_path": str(folder / "timeline-summary.md"),
    }


def write_creative_context(
    settings: Settings,
    production: dict[str, Any],
) -> dict[str, str]:
    """Persist the complete timeline and summaries outside the database."""
    paths = creative_context_paths(settings, production)
    folder = Path(paths["workspace_dir"])
    folder.mkdir(parents=True, exist_ok=True)

    decomposition = production.get("decomposition") or {}
    segments = list(decomposition.get("segments") or [])
    opening = dict(decomposition.get("opening") or {})
    source = dict(production.get("source_video") or {})
    intent = dict(production.get("intent") or {})
    context = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "production_id": production.get("id"),
        "brief": production.get("brief") or "",
        "source_video": source,
        "reference_images": list(production.get("reference_images") or []),
        "summary": decomposition.get("summary") or decomposition.get("description") or "",
        "opening": opening,
        "duration_ms": int(source.get("duration_ms") or 0),
        "segments": segments,
        "references": list(intent.get("references") or []),
        "warnings": list(production.get("warnings") or []),
    }
    _atomic_write_text(
        Path(paths["context_path"]),
        json.dumps(context, ensure_ascii=False, indent=2) + "\n",
    )
    _atomic_write_text(Path(paths["timeline_path"]), _timeline_markdown(context))
    return paths


def _timeline_markdown(context: dict[str, Any]) -> str:
    opening = context.get("opening") or {}
    default_reason = "\u672a\u63d0\u4f9b\u5f00\u573a\u8bf4\u660e"
    lines = [
        f"# \u53c2\u8003\u89c6\u9891\u65f6\u95f4\u8f74 {context.get('production_id') or ''}".rstrip(),
        "",
        "## \u6574\u7247\u6458\u8981",
        "",
        str(context.get("summary") or "\u672a\u63d0\u4f9b\u6574\u7247\u6458\u8981\u3002"),
        "",
        "## \u5f00\u573a\u8fde\u7eed\u5757",
        "",
        (
            f"- \u8303\u56f4\uff1a0-{int(opening.get('end_ms') or 0)}ms"
            f"\uff08{opening.get('method') or 'unknown'}\uff09"
        ),
        f"- \u8bf4\u660e\uff1a{opening.get('reason') or default_reason}",
        "",
        "## \u7247\u6bb5",
        "",
        "| # | ID | \u8303\u56f4 | \u89d2\u8272 | \u6458\u8981 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for index, segment in enumerate(context.get("segments") or [], start=1):
        segment_id = _cell(segment.get("id"))
        role = _cell(segment.get("role"))
        start_ms = int(segment.get("start_ms") or 0)
        end_ms = int(segment.get("end_ms") or 0)
        summary = _cell(
            segment.get("summary")
            or segment.get("description")
            or ""
        )
        lines.append(
            f"| {index} | {segment_id} | {start_ms}-{end_ms}ms | {role} | {summary} |"
        )
    lines.extend(
        [
            "",
            "\u5b8c\u6574\u7ed3\u6784\u5316\u6570\u636e\u89c1\u540c\u76ee\u5f55 `creative-context.json`\u3002",
            "",
        ]
    )
    return "\n".join(lines)


def _cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _atomic_write_text(path: Path, text: str) -> None:
    temp_path: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(text)
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
