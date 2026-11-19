from __future__ import annotations

import json


def _payload(result):
    assert result["success"]
    return json.loads(result["contentItems"][0]["text"])


def test_read_skill_reads_project_skill_file(app_env):
    result = app_env.tools.execute(
        "read_skill",
        {"skill": "production-orchestration"},
        {},
    )

    payload = _payload(result)
    assert payload["slug"] == "production-orchestration"
    assert payload["path"] == "SKILL.md"
    assert "Production Orchestration" in payload["content"]
    assert payload["source"] == "bundled"


def test_read_skill_reads_nested_reference_resource(app_env):
    result = app_env.tools.execute(
        "read_skill",
        {"skill": "skill://video-references/references/index.md"},
        {},
    )

    payload = _payload(result)
    assert payload["path"] == "references/index.md"
    assert payload["content"].strip()


def test_read_skill_rejects_path_escape(app_env):
    result = app_env.tools.execute(
        "read_skill",
        {"skill": "video-references", "path": "../../../../etc/passwd"},
        {},
    )

    assert not result["success"]
    assert "越界" in result["contentItems"][0]["text"]


def test_system_instructions_advertise_server_skill_reader(app_env):
    instructions = app_env.hub._system_instructions({})
    tool_names = {spec["name"] for spec in app_env.tools.specs()}

    assert "read_skill" in instructions
    assert "read_skill" in tool_names
    assert "production-orchestration" in instructions
    assert "不要依赖终端" in instructions
