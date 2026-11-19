"""Internal durable jobs. Inputs are explicitly selected business data, never settings/secrets."""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any, Callable

from .db import Database, utc_now
from .providers import ProviderCancelled


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def revision(production: dict[str, Any]) -> str:
    return digest({key: production.get(key) for key in
                   ("selected_script_id", "script", "source_video", "replace_plan")})


def artifact_exists(result: dict[str, Any]) -> bool:
    path = Path(result.get("video_path") or result.get("path") or "")
    return path.is_file() and path.stat().st_size > 0


class JobRunner:
    def __init__(self, db: Database, production_id: str, cancel: threading.Event):
        self.db, self.production_id, self.cancel = db, production_id, cancel

    def check_cancelled(self) -> None:
        production = self.db.get_production(self.production_id) or {}
        if self.cancel.is_set() or production.get("status") == "cancelled":
            self.cancel.set()
            self.db.cancel_production_jobs(self.production_id)
            raise ProviderCancelled("任务已取消")

    def run(self, tool: str, inputs: dict[str, Any], operation: Callable,
            *, shot_id: str | None = None, validate: Callable = artifact_exists) -> dict[str, Any]:
        self.check_cancelled()
        job = self.db.create_production_job({
            "production_id": self.production_id, "tool": tool, "shot_id": shot_id,
            "idempotency_key": digest([self.production_id, tool, shot_id, inputs]), "input_json": inputs,
        })
        if job["status"] == "cancelled":
            raise ProviderCancelled("该作业已取消，不能重用")
        if job["status"] == "succeeded" and validate(job["result_json"]):
            return job["result_json"]
        self.db.update_production_job(job["id"], {
            "status": "running", "started_at": utc_now(), "finished_at": None,
            "attempt_count": job["attempt_count"] + 1, "error": None,
        })
        try:
            self.check_cancelled()
            result = operation(job)
            self.check_cancelled()
            if not validate(result):
                raise RuntimeError(f"{tool} 作业产物缺失或校验失败")
            self.db.update_production_job(job["id"], {
                "status": "succeeded", "result_json": result, "finished_at": utc_now(),
            })
            self.check_cancelled()
            return result
        except Exception as exc:
            cancelled = self.cancel.is_set() or isinstance(exc, ProviderCancelled)
            self.db.update_production_job(job["id"], {
                "status": "cancelled" if cancelled else "failed",
                "error": ("本地已取消；未取消远端任务" if cancelled else str(exc)), "finished_at": utc_now(),
            })
            raise
