from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from video_fission.db import Database
from video_fission.service import CodexHub
from video_fission.skills import SkillManager
from video_fission.tools import build_registry

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_CODEX") != "1",
    reason="Set RUN_LIVE_CODEX=1 to exercise the installed Codex CLI",
)


def _wait_idle(hub: CodexHub, conversation_id: str, timeout: float = 120) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        conversation = hub.get_conversation(conversation_id)
        if conversation["status"] != "running":
            return conversation
        time.sleep(0.4)
    raise AssertionError("Codex conversation did not become idle before timeout")


def _last_text(conversation: dict, item_type: str) -> str:
    values = [item["payload"].get("text") or "" for item in conversation["items"] if item["item_type"] == item_type]
    return values[-1] if values else ""


def test_live_conversation_skill_discovery_tool_and_persistence(test_settings):
    db = Database(test_settings.database_path)
    skills = SkillManager(
        test_settings.skills_dir,
        test_settings.bundled_skills_dir,
        test_settings.disabled_skills_dir,
    )
    tools = build_registry(db, test_settings.workspace, test_settings.artifacts_dir)
    hub = CodexHub(test_settings, db, skills, tools)
    hub.initialize()
    try:
        runtime_skills = hub.runtime_skills()
        assert any(item.get("name") == "video-brief" for item in runtime_skills["items"])

        conversation = hub.create_conversation(
            {
                "title": "Live E2E",
                "model": "deepseek-flash",
                "reasoning_effort": "low",
            }
        )
        hub.send_message(
            conversation["id"],
            {"text": "请只回复 VIDEO_FISSION_LIVE_OK，用来验证完整对话链路。"},
        )
        completed = _wait_idle(hub, conversation["id"])
        assert completed["status"] == "idle", completed.get("last_error")
        assert "VIDEO_FISSION_LIVE_OK" in _last_text(completed, "agentMessage")

        hub.send_message(
            conversation["id"],
            {
                "text": (
                    "请调用 create_video_brief 工具保存一个 20 秒的"
                    "“城市夜跑”视频简报，受众是跑步新手。完成后告诉我文件路径。"
                )
            },
        )
        completed = _wait_idle(hub, conversation["id"])
        assert completed["status"] == "idle", completed.get("last_error")
        runs = db.list_tool_runs(conversation["id"])
        assert any(run["tool_name"] == "create_video_brief" and run["success"] for run in runs)

        artifact_text = runs[-1]["result"]["contentItems"][0]["text"]
        artifact_path = Path(artifact_text.split("：", 1)[1])
        assert artifact_path.is_file()

        forked = hub.fork(conversation["id"])
        assert forked["codex_thread_id"]
        assert len(hub.get_conversation(forked["id"])["items"]) == len(
            hub.get_conversation(conversation["id"])["items"]
        )

        archived = hub.archive(conversation["id"], True)
        assert archived["is_archived"] is True
        unarchived = hub.archive(conversation["id"], False)
        assert unarchived["is_archived"] is False

        reopened = Database(test_settings.database_path)
        persisted = reopened.get_conversation(conversation["id"])
        assert persisted["codex_thread_id"]
        assert len(reopened.list_items(conversation["id"])) >= 5
        assert reopened.list_events(conversation["id"])
    finally:
        hub.shutdown()
