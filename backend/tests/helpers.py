from __future__ import annotations

from typing import Any


def finish_production(production_id: str, result: dict[str, Any] | None = None, script_id: str | None = None) -> dict[str, Any]:
    """Continue past awaiting_script_choice by selecting a candidate."""
    from video_fission import container

    current = result or container.productions.get(production_id)
    if current and current.get("status") == "awaiting_script_choice":
        candidates = current.get("script_candidates") or []
        chosen = script_id or (candidates[0].get("id") if candidates else "script_a")
        current = container.productions.select_script(production_id, chosen, wait=True)
    return current
