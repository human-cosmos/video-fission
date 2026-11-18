from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from video_fission import container
from video_fission.container import configure
from video_fission.db import Database
from video_fission.main import app
from video_fission.sso import sso_service


def _login(client: TestClient, username: str, password: str) -> dict:
    response = client.post(
        "/api/auth/login",
        data={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_login_and_user_isolated_conversations_and_settings(test_settings):
    secure_settings = replace(
        test_settings,
        auth_required=True,
        auth_secret_key="test-only-secret-key-with-enough-entropy",
        auth_admin_username="admin",
        auth_admin_password="admin-password-123",
    )
    configure(secure_settings)

    with TestClient(app) as anonymous:
        assert anonymous.get("/api/health").status_code == 200
        assert anonymous.get("/api/conversations").status_code == 401

    with TestClient(app) as admin_client:
        admin = _login(admin_client, "admin", "admin-password-123")
        assert admin_client.get("/api/auth/me").json()["username"] == "admin"
        for username in ("alice", "bob"):
            response = admin_client.post(
                "/api/users",
                headers={"Authorization": f"Bearer {admin['access_token']}"},
                json={
                    "username": username,
                    "password": f"{username}-password",
                    "display_name": username.title(),
                    "role": "user",
                },
            )
            assert response.status_code == 201, response.text

    with TestClient(app) as alice_client, TestClient(app) as bob_client:
        alice = _login(alice_client, "alice", "alice-password")
        bob = _login(bob_client, "bob", "bob-password")
        alice_headers = {"Authorization": f"Bearer {alice['access_token']}"}
        bob_headers = {"Authorization": f"Bearer {bob['access_token']}"}

        alice_conversation = alice_client.post(
            "/api/conversations",
            headers=alice_headers,
            json={"title": "Alice private"},
        ).json()
        bob_conversation = bob_client.post(
            "/api/conversations",
            headers=bob_headers,
            json={"title": "Bob private"},
        ).json()
        assert alice_client.post(
            "/api/conversations",
            headers={**alice_headers, "Origin": "https://evil.example"},
            json={"title": "Cross-site request"},
        ).status_code == 403

        alice_items = alice_client.get("/api/conversations", headers=alice_headers).json()["items"]
        bob_items = bob_client.get("/api/conversations", headers=bob_headers).json()["items"]
        assert [item["id"] for item in alice_items] == [alice_conversation["id"]]
        assert [item["id"] for item in bob_items] == [bob_conversation["id"]]
        assert bob_client.get(
            f"/api/conversations/{alice_conversation['id']}",
            headers=bob_headers,
        ).status_code == 404

        alice_settings = alice_client.put(
            "/api/settings",
            headers=alice_headers,
            json={"multimodal": {"model": "alice-model", "api_key": "alice-private-key"}},
        )
        assert alice_settings.status_code == 200
        assert alice_settings.json()["multimodal"]["model"] == "alice-model"
        assert alice_settings.json()["multimodal"]["api_key_set"] is True
        bob_settings = bob_client.get("/api/settings", headers=bob_headers)
        assert bob_settings.status_code == 200
        assert bob_settings.json()["multimodal"]["model"] == ""
        assert bob_settings.json()["multimodal"]["api_key_set"] is False

        alice_workspace = Path(alice_conversation["cwd"])
        bob_workspace = Path(bob_conversation["cwd"])
        assert alice_workspace != bob_workspace
        assert secure_settings.workspace in alice_workspace.parents
        assert secure_settings.workspace in bob_workspace.parents
        legacy_attachment = secure_settings.uploads_dir / "legacy-private.txt"
        legacy_attachment.write_text("legacy admin file", encoding="utf-8")
        with pytest.raises(ValueError, match="附件不存在"):
            container.hub._validated_files(
                [{"name": "legacy-private.txt", "path": str(legacy_attachment)}],
                alice["user"]["id"],
                allow_legacy=False,
            )


def test_production_and_uploads_cannot_cross_user_boundaries(test_settings):
    secure_settings = replace(
        test_settings,
        auth_required=True,
        auth_secret_key="test-only-secret-key-with-enough-entropy",
        auth_admin_username="admin",
        auth_admin_password="admin-password-123",
    )
    configure(secure_settings)
    sample = secure_settings.project_root / "backend" / "tests" / "fixtures" / "sample-reference.mp4"

    with TestClient(app) as admin_client:
        admin = _login(admin_client, "admin", "admin-password-123")
        headers = {"Authorization": f"Bearer {admin['access_token']}"}
        for username in ("alice", "bob"):
            assert admin_client.post(
                "/api/users",
                headers=headers,
                json={
                    "username": username,
                    "password": f"{username}-password",
                    "role": "user",
                },
            ).status_code == 201

    with TestClient(app) as alice_client, TestClient(app) as bob_client:
        alice = _login(alice_client, "alice", "alice-password")
        bob = _login(bob_client, "bob", "bob-password")
        alice_headers = {"Authorization": f"Bearer {alice['access_token']}"}
        bob_headers = {"Authorization": f"Bearer {bob['access_token']}"}
        alice_conversation = alice_client.post(
            "/api/conversations",
            headers=alice_headers,
            json={},
        ).json()
        with sample.open("rb") as handle:
            upload = alice_client.post(
                "/api/productions/uploads",
                headers=alice_headers,
                files={"file": ("reference.mp4", handle, "video/mp4")},
            )
        assert upload.status_code == 201, upload.text
        upload_payload = upload.json()
        assert "/uploads/users/" in Path(upload_payload["path"]).as_posix()
        production = alice_client.post(
            "/api/productions",
            headers=alice_headers,
            json={
                "brief": "Alice private production",
                "video_upload_id": upload_payload["id"],
                "conversation_id": alice_conversation["id"],
            },
        )
        assert production.status_code == 201, production.text
        production_id = production.json()["id"]

        assert bob_client.get(
            f"/api/productions/{production_id}",
            headers=bob_headers,
        ).status_code == 404
        assert bob_client.get(
            f"/api/productions/uploads/{upload_payload['id']}",
            headers=bob_headers,
        ).status_code == 404
        assert bob_client.get("/api/productions", headers=bob_headers).json()["items"] == []


def test_sso_syncs_cas_identity_into_local_user(test_settings, monkeypatch):
    secure_settings = replace(
        test_settings,
        auth_required=True,
        auth_secret_key="test-only-secret-key-with-enough-entropy",
        auth_admin_password="admin-password-123",
        sso_client_id="video-fission",
        sso_client_secret="sso-secret",
        sso_client_name="VideoFission",
        sso_cas_auth_url="https://sso.example.test/auth",
        sso_business_api_url="https://sso.example.test/api",
        sso_access_token_url="https://sso.example.test/token",
    )
    configure(secure_settings)
    assert sso_service.build_auth_url("https://video.example.test/api/sso/callback") == (
        "https://sso.example.test/auth?"
        "client=video-fission&redirect=https%3A%2F%2Fvideo.example.test%2Fapi%2Fsso%2Fcallback"
    )
    with TestClient(app) as client:
        rejected = client.get("/api/sso/login", headers={"Host": "evil.example"})
        assert rejected.status_code == 503
        accepted = client.get("/api/sso/login", headers={"Host": "127.0.0.1"})
        assert accepted.status_code == 200
        assert "redirect=http%3A%2F%2F127.0.0.1%2Fapi%2Fsso%2Fcallback" in accepted.json()["auth_url"]
    monkeypatch.setattr(sso_service, "get_access_token", lambda: "client-token")
    monkeypatch.setattr(sso_service, "check_ticket", lambda _token, _ticket: "login-1")
    monkeypatch.setattr(
        sso_service,
        "get_user_data",
        lambda _token, _login: {
            "id": 9001,
            "name": "Enterprise User",
            "account": "enterprise.user",
            "areaId": 42,
        },
    )

    result = sso_service.verify_and_login("cas-ticket")
    assert result["user"]["username"] == "enterprise.user"
    assert result["user"]["sso_user_id"] == 9001
    assert result["user"]["role"] == "user"
    from video_fission.auth import user_from_token

    assert user_from_token(result["access_token"])["username"] == "enterprise.user"


def test_password_reset_revokes_existing_token(test_settings):
    secure_settings = replace(
        test_settings,
        auth_required=True,
        auth_secret_key="test-only-secret-key-with-enough-entropy",
        auth_admin_password="admin-password-123",
    )
    configure(secure_settings)

    with TestClient(app) as admin_client, TestClient(app) as alice_client:
        admin = _login(admin_client, "admin", "admin-password-123")
        admin_headers = {"Authorization": f"Bearer {admin['access_token']}"}
        created = admin_client.post(
            "/api/users",
            headers=admin_headers,
            json={
                "username": "alice",
                "password": "alice-password",
                "role": "user",
            },
        ).json()
        alice = _login(alice_client, "alice", "alice-password")
        alice_headers = {"Authorization": f"Bearer {alice['access_token']}"}
        assert alice_client.get("/api/auth/me", headers=alice_headers).status_code == 200

        reset = admin_client.patch(
            f"/api/users/{created['id']}",
            headers=admin_headers,
            json={"password": "alice-password-new"},
        )
        assert reset.status_code == 200
        assert alice_client.get("/api/auth/me", headers=alice_headers).status_code == 401
        _login(alice_client, "alice", "alice-password-new")


def test_recreated_username_does_not_accept_old_user_token(test_settings):
    secure_settings = replace(
        test_settings,
        auth_required=True,
        auth_secret_key="test-only-secret-key-with-enough-entropy",
        auth_admin_password="admin-password-123",
    )
    configure(secure_settings)

    with TestClient(app) as admin_client, TestClient(app) as alice_client:
        admin = _login(admin_client, "admin", "admin-password-123")
        admin_headers = {"Authorization": f"Bearer {admin['access_token']}"}
        old_user = admin_client.post(
            "/api/users",
            headers=admin_headers,
            json={"username": "alice", "password": "alice-password"},
        ).json()
        old_token = _login(alice_client, "alice", "alice-password")["access_token"]
        assert admin_client.delete(
            f"/api/users/{old_user['id']}",
            headers=admin_headers,
        ).status_code == 200
        assert admin_client.post(
            "/api/users",
            headers=admin_headers,
            json={"username": "alice", "password": "alice-password-new"},
        ).status_code == 201

        assert alice_client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {old_token}"},
        ).status_code == 401


def test_custom_skills_are_global_between_authenticated_users(test_settings):
    secure_settings = replace(
        test_settings,
        auth_required=True,
        auth_secret_key="test-only-secret-key-with-enough-entropy",
        auth_admin_password="admin-password-123",
    )
    configure(secure_settings)

    with TestClient(app) as admin_client:
        admin = _login(admin_client, "admin", "admin-password-123")
        admin_headers = {"Authorization": f"Bearer {admin['access_token']}"}
        for username in ("alice", "bob"):
            assert admin_client.post(
                "/api/users",
                headers=admin_headers,
                json={"username": username, "password": f"{username}-password"},
            ).status_code == 201

    with TestClient(app) as alice_client, TestClient(app) as bob_client:
        alice = _login(alice_client, "alice", "alice-password")
        bob = _login(bob_client, "bob", "bob-password")
        alice_headers = {"Authorization": f"Bearer {alice['access_token']}"}
        bob_headers = {"Authorization": f"Bearer {bob['access_token']}"}
        created = alice_client.post(
            "/api/skills",
            headers=alice_headers,
            json={
                "slug": "global-hook",
                "name": "Global Hook",
                "description": "A shared skill for every authenticated user.",
                "body": "Use the shared workflow.",
            },
        )
        assert created.status_code == 201, created.text

        alice_slugs = {
            item["slug"]
            for item in alice_client.get("/api/skills", headers=alice_headers).json()["items"]
        }
        bob_slugs = {
            item["slug"]
            for item in bob_client.get("/api/skills", headers=bob_headers).json()["items"]
        }
        assert "global-hook" in alice_slugs
        assert "global-hook" in bob_slugs
        assert bob_client.get("/api/skills/global-hook", headers=bob_headers).status_code == 200


def test_sso_cannot_take_over_unlinked_local_admin(test_settings, monkeypatch):
    secure_settings = replace(
        test_settings,
        auth_required=True,
        auth_secret_key="test-only-secret-key-with-enough-entropy",
        auth_admin_username="enterprise.user",
        auth_admin_password="admin-password-123",
        sso_client_id="video-fission",
        sso_client_secret="sso-secret",
        sso_cas_auth_url="https://sso.example.test/auth",
        sso_business_api_url="https://sso.example.test/api",
    )
    configure(secure_settings)
    monkeypatch.setattr(sso_service, "get_access_token", lambda: "client-token")
    monkeypatch.setattr(sso_service, "check_ticket", lambda _token, _ticket: "login-1")
    monkeypatch.setattr(
        sso_service,
        "get_user_data",
        lambda _token, _login: {
            "id": 9001,
            "name": "Enterprise User",
            "account": "enterprise.user",
            "areaId": 42,
        },
    )

    with pytest.raises(RuntimeError, match="未绑定当前企业身份"):
        sso_service.verify_and_login("cas-ticket")
    admin = container.db.get_user_by_username("enterprise.user")
    assert admin["role"] == "admin"
    assert admin["sso_user_id"] is None


def test_auth_bootstrap_adopts_legacy_data_and_settings(test_settings):
    db = Database(test_settings.database_path)
    db.initialize()
    legacy = db.create_conversation({"cwd": str(test_settings.workspace)})
    db.upsert_app_settings(
        {"multimodal": {"provider": "openai_compatible", "model": "legacy-model"}},
        {"multimodal": "legacy-key"},
    )
    secure_settings = replace(
        test_settings,
        auth_required=True,
        auth_secret_key="test-only-secret-key-with-enough-entropy",
        auth_admin_password="admin-password-123",
    )

    configure(secure_settings)
    admin = container.db.get_user_by_username("admin")
    assert container.db.get_conversation(legacy["id"], admin["id"]) is not None
    migrated_settings = container.db.get_user_settings(admin["id"])
    assert migrated_settings["public"]["multimodal"]["model"] == "legacy-model"
    assert migrated_settings["secrets"]["multimodal"] == "legacy-key"
