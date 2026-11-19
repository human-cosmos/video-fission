from __future__ import annotations

import json
from pathlib import Path

from video_fission.db import Database
from video_fission.script_agent import VIDEO_CREATIVE_SKILLS, VideoScriptAgent
from video_fission.skills import SkillManager


def test_video_script_agent_uses_skills_and_returns_json(test_settings, monkeypatch):
    from video_fission import script_agent as script_agent_module

    expected = {
        "intent": {
            "goal": "突出石墨烯发热除雪效率",
            "audience": "北美有车家庭",
            "promise": "数秒吹散车窗积雪",
            "assumptions": ["视频展示的是真实除雪效果"],
        },
        "skeleton": {
            "one_liner": "手持热能机快速清除车窗积雪",
            "hook": "厚雪盖住车窗，直接开机吹散",
            "value": "展示出风口、雪雾和露出玻璃的变化",
            "cta": "看到玻璃恢复清晰后提示了解产品",
        },
    }
    captured: dict = {}

    class FakeRuntime:
        running = True

        def __init__(self, _codex_bin, **kwargs):
            captured["runtime"] = kwargs

        def start(self):
            return self

        def initialize(self):
            return {}

        def set_skill_roots(self, roots):
            captured["skill_roots"] = roots

        def start_thread(self, payload):
            captured["thread_payload"] = payload
            return {"thread": {"id": "thr_script"}}

        def start_turn(self, turn_payload):
            captured["turn_payload"] = turn_payload
            self.runtime_kwargs["on_notification"](
                "item/completed",
                {
                    "itemId": "msg_script",
                    "item": {
                        "id": "msg_script",
                        "type": "agentMessage",
                        "text": json.dumps(expected, ensure_ascii=False),
                    },
                },
            )
            self.runtime_kwargs["on_notification"](
                "turn/completed",
                {"turn": {"id": "turn_script", "status": "completed"}},
            )
            return {"turn": {"id": "turn_script", "status": "completed"}}

        def interrupt(self, _thread_id, _turn_id):
            return {}

        def close(self):
            self.running = False

    original_init = FakeRuntime.__init__

    def init(self, codex_bin, **kwargs):
        original_init(self, codex_bin, **kwargs)
        self.runtime_kwargs = kwargs

    FakeRuntime.__init__ = init
    monkeypatch.setattr(script_agent_module, "CodexAppServer", FakeRuntime)

    database = Database(test_settings.database_path)
    database.initialize()
    skills = SkillManager(
        test_settings.skills_dir,
        test_settings.bundled_skills_dir,
        test_settings.disabled_skills_dir,
    )
    agent = VideoScriptAgent(test_settings, database, skills)
    artifact_dir = test_settings.artifacts_dir / "agent-test"
    production = {
        "id": "prd_agenttest",
        "user_id": None,
        "conversation_id": None,
        "brief": "做一条除雪机实测视频",
        "landing_page": {},
        "decomposition": {
            "summary": "车窗积雪被热风吹散",
            "segments": [
                {
                    "id": "seg_01",
                    "role": "hook",
                    "start_ms": 0,
                    "end_ms": 3000,
                    "summary": "车窗厚雪特写",
                }
            ],
        },
        "_artifact_dir": str(artifact_dir),
    }

    result = agent.generate_intent(
        production,
        [{"path": "skills/video-references/SKILL.md", "why": "检索参考结构"}],
    )

    assert result == expected
    assert captured["skill_roots"] == skills.runtime_roots
    mentions = [item["path"] for item in captured["turn_payload"]["input"] if item.get("type") == "mention"]
    assert mentions == [f"skill://{slug}" for slug in VIDEO_CREATIVE_SKILLS]
    assert "creative-intent-script-skeleton" in captured["turn_payload"]["input"][0]["text"]
    assert (artifact_dir / "agent_intent.txt").is_file()
