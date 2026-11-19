from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.conftest import FIXTURE_VIDEO
from video_fission.production_agent import FakeCodexRuntime

ORCH_SKILLS = [
    "production-orchestration",
    "video-references",
    "creative-intent-script-skeleton",
    "universal-ugc-video-script-generation",
]


def upload_fixture(productions) -> dict[str, Any]:
    data = FIXTURE_VIDEO.read_bytes()
    return productions.save_upload("sample-reference.mp4", "video/mp4", [data])


def create_production(productions, brief: str = "做一个开场抓人的产品片，全部新生成") -> dict[str, Any]:
    upload = upload_fixture(productions)
    return productions.create(None, brief, upload["id"])


def stub_candidates() -> list[dict[str, Any]]:
    voiceovers = ["钩子A 卖点A 行动A", "钩子B 卖点B 行动B", "钩子C 卖点C 行动C"]
    candidates = []
    for index, voiceover in enumerate(voiceovers, start=1):
        script_id = f"script_{index}"
        candidates.append(
            {
                "id": script_id,
                "title": f"方案 {index}",
                "angle": f"角度{index}",
                "script": {
                    "shots": [
                        {"voiceover": voiceover, "on_screen_text": f"字幕{index}"},
                    ]
                },
            }
        )
    return candidates


def plan_until_scripts(candidates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "skills": list(ORCH_SKILLS),
        "intent": {"goal": "测", "audience": "测", "promise": "测", "assumptions": []},
        "skeleton": {"one_liner": "测", "hook": "钩", "value": "值", "cta": "行动"},
        "script_candidates": candidates or stub_candidates(),
        "steps": [
            {"tool": "ingest_reference_video"},
            {"tool": "understand_reference_video"},
            {"tool": "extract_landing_points"},
            {"tool": "search_video_references"},
            {"tool": "lock_timeline_opening"},
            {"tool": "write_intent_and_scripts"},
        ],
    }


def plan_with_premature_generate() -> dict[str, Any]:
    plan = plan_until_scripts()
    plan["steps"] = list(plan["steps"]) + [{"tool": "generate_video_shots"}]
    return plan


def inject_fake(app_env, plan: dict[str, Any] | None = None, *, available: bool = True) -> FakeCodexRuntime:
    runtime = FakeCodexRuntime(plan, available=available)
    app_env.productions.agent.runtime = runtime
    return runtime


def called_tools(production: dict[str, Any]) -> list[str]:
    return [item.get("name") for item in (production.get("agent_trace") or []) if item.get("type") == "tool"]


def seed_clips_from_fixture(app_env, production_id: str) -> None:
    import shutil

    production = app_env.db.get_production(production_id)
    art = app_env.productions.engine.artifact_dir(production_id)
    art.mkdir(parents=True, exist_ok=True)
    clips = []
    for shot in (production.get("script") or {}).get("shots") or []:
        dest = art / f"{shot['id']}.mp4"
        shutil.copyfile(FIXTURE_VIDEO, dest)
        clips.append({"shot_id": shot["id"], "path": str(dest), "provider": "fixture"})
    app_env.db.update_production(production_id, {"clips": clips})
