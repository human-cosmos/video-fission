from __future__ import annotations


def test_approval_requests_are_auto_accepted_without_pending_cards(app_env):
    conversation = app_env.hub.create_conversation({"title": "自动审批"})

    response = app_env.hub._handle_approval(
        conversation["id"],
        "req_command",
        "item/commandExecution/requestApproval",
        {
            "command": ["rg", "-n", "timeline", "."],
            "cwd": conversation["cwd"],
            "reason": "只读检索",
        },
    )

    assert response == {"decision": "accept"}
    assert app_env.db.pending_approvals(conversation["id"]) == []
    kinds = {item["kind"] for item in app_env.db.list_events(conversation["id"])}
    assert "approval.auto_approved" in kinds
    assert "approval.requested" not in kinds


def test_existing_pending_approvals_are_resolved_on_load(app_env):
    conversation = app_env.hub.create_conversation({"title": "历史审批"})
    approval = app_env.db.create_approval(
        conversation["id"],
        "req_legacy",
        "item/fileChange/requestApproval",
        {"kind": "file", "paths": ["README.md"], "reason": "只读检索"},
        {},
    )

    loaded = app_env.hub.get_conversation(conversation["id"])

    assert loaded["pending_approvals"] == []
    resolved = app_env.db.get_approval(approval["id"])
    assert resolved["decision"] == "accept"
