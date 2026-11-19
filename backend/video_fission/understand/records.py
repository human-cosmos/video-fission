"""Persist individual Gemini API responses for diagnosis and replay."""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SAFE_LABEL = re.compile(r"[^A-Za-z0-9_.-]+")


class GeminiCallRecorder:
    """Write one JSON file per Gemini understanding or clip-analysis call."""

    def __init__(
        self,
        artifact_dir: Path,
        call_type: str,
        *,
        provider: str = "gemini_gateway",
        model: str = "",
    ) -> None:
        self.call_type = call_type
        self.provider = provider
        self.model = model
        self.directory = Path(artifact_dir) / "gemini_api_calls" / call_type

    def record(
        self,
        *,
        label: str,
        input_mode: str,
        status: str,
        elapsed_ms: float,
        response: Any = None,
        error: BaseException | None = None,
    ) -> Path:
        recorded_at = datetime.now(UTC)
        safe_label = _SAFE_LABEL.sub("_", str(label or "call")).strip("._") or "call"
        filename = (
            f"{recorded_at.strftime('%Y%m%dT%H%M%S_%fZ')}_"
            f"{safe_label}_{uuid.uuid4().hex[:8]}.json"
        )
        payload: dict[str, Any] = {
            "schema_version": 1,
            "call_type": self.call_type,
            "call_label": safe_label,
            "provider": self.provider,
            "model": self.model,
            "input_mode": input_mode,
            "status": status,
            "recorded_at": recorded_at.isoformat(),
            "elapsed_ms": round(max(float(elapsed_ms), 0.0), 1),
        }
        if error is not None:
            payload["error"] = {
                "type": error.__class__.__name__,
                "message": str(error or ""),
            }
        if response is not None:
            payload["response"] = response

        self.directory.mkdir(parents=True, exist_ok=True)
        output_path = self.directory / filename
        temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
        try:
            with temporary_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temporary_path, output_path)
        finally:
            temporary_path.unlink(missing_ok=True)
        logger.info(
            "gemini_api_result_saved type=%s status=%s label=%s output=%s",
            self.call_type,
            status,
            safe_label,
            output_path,
        )
        return output_path
