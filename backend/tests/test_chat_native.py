from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from video_fission.container import configure
from video_fission.main import app
from video_fission.tools import build_registry


@pytest.fixture
def client(test_settings):
    configure(test_settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client_no_codex(test_settings):
    configure(replace(test_settings, codex_bin=""))
    with TestClient(app) as test_client:
        yield test_client

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample-reference.mp4"


def _upload(client: TestClient) -> dict:
    with FIXTURE.open("rb") as handle:
        response = client.post(
            "/api/productions/uploads",
            files={"file": ("sample-reference.mp4", handle, "video/mp4")},
        )
    assert response.status_code == 201, response.text
    return response.json()


def _conversation(client: TestClient) -> dict:
    response = client.post("/api/conversations", json={"title": "创作对话"})
    assert response.status_code in {200, 201}, response.text
    return response.json()


def _video_file(upload: dict) -> dict:
    return {
        "kind": "production_video",
        "id": upload["id"],
        "name": upload["name"],
        "path": "",
        "size": upload["size"],
        "content_type": upload["content_type"],
        "duration_ms": upload["duration_ms"],
        "width": upload["width"],
        "height": upload["height"],
    }


def _wait_production(client: TestClient, production_id: str, timeout: float = 180) -> dict:
    from video_fission import container

    deadline = time.time() + timeout
    thread = container.productions._threads.get(production_id)
    if thread is not None:
        thread.join(timeout=timeout)
    while time.time() < deadline:
        body = client.get(f"/api/productions/{production_id}")
        assert body.status_code == 200, body.text
        production = body.json()
        if production["status"] in {"completed", "failed", "cancelled"}:
            return production
        time.sleep(0.25)
    raise AssertionError(f"production {production_id} did not finish")


def test_conversation_id_create_list_bind_and_reject_rebind(client: TestClient):
    upload = _upload(client)
    conversation = _conversation(client)
    other = _conversation(client)

    created = client.post(
        "/api/productions",
        json={
            "title": "unbound",
            "brief": "先创建一个未绑定的任务",
            "video_upload_id": upload["id"],
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["conversation_id"] is None
    production_id = body["id"]

    listed = client.get("/api/productions").json()["items"]
    assert any(item["id"] == production_id and item["conversation_id"] is None for item in listed)

    bound = client.patch(
        f"/api/productions/{production_id}",
        json={"conversation_id": conversation["id"]},
    )
    assert bound.status_code == 200, bound.text
    assert bound.json()["conversation_id"] == conversation["id"]

    filtered = client.get("/api/productions", params={"conversation_id": conversation["id"]}).json()["items"]
    assert [item["id"] for item in filtered] == [production_id]

    empty = client.get("/api/productions", params={"conversation_id": other["id"]}).json()["items"]
    assert empty == []

    rebind = client.patch(
        f"/api/productions/{production_id}",
        json={"conversation_id": other["id"]},
    )
    assert rebind.status_code == 409
    assert "不能改到另一个对话" in rebind.text

    same = client.patch(
        f"/api/productions/{production_id}",
        json={"conversation_id": conversation["id"]},
    )
    assert same.status_code == 200, same.text
    assert same.json()["conversation_id"] == conversation["id"]

    media = client.get(f"/api/productions/uploads/{upload['id']}")
    assert media.status_code == 200, media.text
    assert media.headers["content-type"].startswith("video/")
    assert upload.get("url") == f"/api/productions/uploads/{upload['id']}"


def test_send_message_video_only_does_not_start(client: TestClient):
    upload = _upload(client)
    conversation = _conversation(client)
    response = client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"text": "", "files": [_video_file(upload)]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body.get("production_id") in {None, ""}
    listed = client.get("/api/productions", params={"conversation_id": conversation["id"]}).json()["items"]
    assert listed == []
    items = client.get(f"/api/conversations/{conversation['id']}").json()["items"]
    user_messages = [item for item in items if item["item_type"] == "userMessage"]
    assert len(user_messages) == 1
    assert user_messages[0]["payload"]["files"][0]["kind"] == "production_video"
    assert user_messages[0]["payload"]["files"][0]["url"].endswith(upload["id"])


def test_send_message_auto_starts_upserts_cards_and_dedups_tool(client: TestClient, test_settings):
    upload = _upload(client)
    conversation = _conversation(client)
    brief = "用夹具做一个开场抓人、中间展示产品、结尾促单的短视频"
    response = client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"text": brief, "files": [_video_file(upload)], "replace_policy": {"mode": "brief"}},
    )
    assert response.status_code == 200, response.text
    production_id = response.json()["production_id"]
    assert production_id

    detail = client.get(f"/api/productions/{production_id}").json()
    assert detail["conversation_id"] == conversation["id"]
    assert detail["brief"] == brief

    from video_fission import container

    tools = build_registry(container.db, test_settings.workspace, test_settings.artifacts_dir, container.productions)
    again = tools.execute(
        "start_video_production",
        {"brief": brief, "video_upload_id": upload["id"]},
        {"conversation_id": conversation["id"]},
    )
    assert again["success"] is True
    payload = again["contentItems"][0]["text"]
    assert production_id in payload

    finished = _wait_production(client, production_id)
    assert finished["status"] == "completed", finished

    conversation_body = client.get(f"/api/conversations/{conversation['id']}").json()
    items = {item["id"]: item for item in conversation_body["items"]}
    expected = {
        f"{production_id}:progress": "video_progress",
        f"{production_id}:understanding": "video_understanding",
        f"{production_id}:plan": "video_plan",
        f"{production_id}:segments": "video_segments",
        f"{production_id}:output": "video_output",
    }
    for item_id, item_type in expected.items():
        assert item_id in items, item_id
        card = items[item_id]
        assert card["item_type"] == item_type
        assert card["codex_item_id"] is None
        assert card["payload"]["production_id"] == production_id
    assert items[f"{production_id}:output"]["payload"]["video_url"].endswith("/final.mp4")
    assert items[f"{production_id}:output"]["payload"].get("updated_at")
    assert items[f"{production_id}:segments"]["payload"].get("status") == "completed"
    assert items[f"{production_id}:understanding"]["payload"]["source_video_url"].endswith(upload["id"])

    events = container.db.list_events(conversation["id"])
    kinds = {event["kind"] for event in events}
    assert "item_upserted" in kinds
    assert "production_updated" in kinds


def test_text_followup_without_codex_reruns_brief(client_no_codex: TestClient):
    upload = _upload(client_no_codex)
    conversation = _conversation(client_no_codex)
    created = client_no_codex.post(
        "/api/productions",
        json={
            "title": "followup",
            "brief": "先跑一遍默认创意",
            "video_upload_id": upload["id"],
            "conversation_id": conversation["id"],
        },
    )
    assert created.status_code == 201, created.text
    production_id = created.json()["id"]

    from video_fission import container

    first = container.productions.engine.run(production_id)
    assert first["status"] == "completed"

    follow = "把第二段和结尾重新生成"
    response = client_no_codex.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"text": follow},
    )
    assert response.status_code == 200, response.text
    assert response.json()["production_id"] == production_id
    updated = _wait_production(client_no_codex, production_id)
    assert updated["status"] == "completed", updated
    assert updated["brief"] == follow
    assert updated["replace_policy"]["mode"] == "brief"

    names = {tool["name"] for tool in container.tools.summary()}
    assert "run_video_production" in names
    assert "start_video_production" in names


def test_run_video_production_tool_forwards_run_body(client: TestClient, test_settings, monkeypatch):
    from video_fission import container

    called: dict = {}

    def fake_run(production_id, wait=False, replace_policy=None, resume_from=None):
        called.update(
            {
                "production_id": production_id,
                "wait": wait,
                "replace_policy": replace_policy,
                "resume_from": resume_from,
            }
        )
        return {"id": production_id, "status": "running"}

    monkeypatch.setattr(container.productions, "run", fake_run)
    result = container.tools.execute(
        "run_video_production",
        {
            "production_id": "prd_testdedup000000000000",
            "resume_from": "intent",
            "replace_policy": {"mode": "brief"},
        },
        {"conversation_id": "conv-1"},
    )
    assert result["success"] is True
    assert called["production_id"] == "prd_testdedup000000000000"
    assert called["resume_from"] == "intent"
    assert called["replace_policy"]["mode"] == "brief"
