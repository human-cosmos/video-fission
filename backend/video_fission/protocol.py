from __future__ import annotations

from collections.abc import Iterable
from typing import Any

ITEM_TYPES = {
    "agentMessage",
    "userMessage",
    "reasoning",
    "plan",
    "commandExecution",
    "fileChange",
    "mcpToolCall",
    "dynamicToolCall",
    "functionCallOutput",
    "collabAgentToolCall",
    "subAgentActivity",
    "webSearch",
    "imageView",
    "imageGeneration",
    "contextCompaction",
    "tokenUsage",
}

EVENT_KIND_BY_METHOD = {
    "turn/started": "turn.started",
    "turn/completed": "turn.completed",
    "turn/diff/updated": "turn.diff.updated",
    "turn/plan/updated": "turn.plan.updated",
    "item/started": "item.started",
    "item/completed": "item.completed",
    "item/agentMessage/delta": "agent.message.delta",
    "item/reasoning/summaryTextDelta": "reasoning.summary.delta",
    "item/reasoning/textDelta": "reasoning.text.delta",
    "item/plan/delta": "plan.delta",
    "item/commandExecution/outputDelta": "command.output.delta",
    "item/commandExecution/terminalInteraction": "command.output.delta",
    "process/outputDelta": "command.output.delta",
    "process/exited": "command.completed",
    "item/fileChange/patchUpdated": "fileChange.patchUpdated",
    "item/fileChange/outputDelta": "fileChange.patchUpdated",
    "item/mcpToolCall/progress": "mcpTool.progress",
    "thread/tokenUsage/updated": "tokenUsage.updated",
    "thread/name/updated": "thread.name.updated",
    "thread/status/changed": "thread.status.changed",
    "mcpServer/startupStatus/updated": "mcp.status.updated",
    "model/rerouted": "model.rerouted",
    "thread/compacted": "compaction.completed",
    "warning": "warning",
    "error": "error",
}

ITEM_TYPE_BY_METHOD = {
    "item/agentMessage/delta": "agentMessage",
    "item/reasoning/summaryTextDelta": "reasoning",
    "item/reasoning/textDelta": "reasoning",
    "item/plan/delta": "plan",
    "item/commandExecution/outputDelta": "commandExecution",
    "item/commandExecution/terminalInteraction": "commandExecution",
    "process/outputDelta": "commandExecution",
    "item/fileChange/patchUpdated": "fileChange",
    "item/fileChange/outputDelta": "fileChange",
    "item/mcpToolCall/progress": "mcpToolCall",
}

APPROVAL_METHODS = {
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
    "item/permissions/requestApproval",
    "item/skill/requestApproval",
    "execCommandApproval",
    "applyPatchApproval",
    "skillApproval",
}


def event_kind(method: str) -> str:
    return EVENT_KIND_BY_METHOD.get(method, "runtime.event")


def item_type_for_method(method: str) -> str | None:
    return ITEM_TYPE_BY_METHOD.get(method)


def normalize_item_type(item: dict[str, Any] | None) -> str | None:
    if not item:
        return None
    value = str(item.get("type") or "")
    return value if value in ITEM_TYPES else None


def extract_item_id(params: dict[str, Any]) -> str | None:
    item_id = params.get("itemId")
    if item_id:
        return str(item_id)
    item = params.get("item")
    if isinstance(item, dict) and item.get("id"):
        return str(item["id"])
    return None


def build_input(
    text: str,
    mentions: Iterable[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = [{"type": "text", "text": text or "", "text_elements": []}]
    for mention in mentions or []:
        name = str(mention.get("name") or "").strip()
        path = str(mention.get("path") or "").strip()
        if not name or not path:
            continue
        if not path.lower().startswith(("app://", "plugin://", "skill://")):
            continue
        parts.append({"type": "mention", "name": name, "path": path})
    return parts


def text_delta(params: dict[str, Any]) -> str:
    value = params.get("delta")
    if isinstance(value, dict):
        return str(value.get("text") or value.get("delta") or "")
    return str(value or "")


def accumulate_payload(
    item_type: str,
    current: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    merged = {**current, **{key: value for key, value in params.items() if key != "delta"}}
    delta = text_delta(params)
    if item_type in {"agentMessage", "reasoning", "plan"}:
        key = "summary" if item_type == "reasoning" else "text"
        merged[key] = str(current.get(key) or current.get("text") or "") + delta
    elif item_type == "commandExecution":
        merged["output"] = str(current.get("output") or "") + delta
        if params.get("command"):
            merged["command"] = params["command"]
    elif item_type == "fileChange" and params.get("patch"):
        merged["patch"] = params["patch"]
    return merged


def flatten_text(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return ""

    def gather(value: Any, chunks: list[str]) -> None:
        if isinstance(value, str):
            if value.strip():
                chunks.append(value.strip())
            return
        if isinstance(value, list):
            for child in value:
                gather(child, chunks)
            return
        if isinstance(value, dict):
            for key in ("text", "delta", "summary", "output", "content"):
                if key in value:
                    gather(value[key], chunks)

    for key in ("text", "summary", "output", "content", "delta"):
        chunks: list[str] = []
        gather(payload.get(key), chunks)
        if chunks:
            return "\n".join(chunks)
    return ""


def classify_approval(method: str, params: dict[str, Any], workspace: str) -> dict[str, Any]:
    if method in {"item/commandExecution/requestApproval", "execCommandApproval"}:
        command = params.get("command")
        if isinstance(command, list):
            command = " ".join(str(part) for part in command)
        return {
            "kind": "command",
            "command": str(command or ""),
            "cwd": str(params.get("cwd") or workspace),
            "reason": params.get("reason"),
            "network": params.get("networkApprovalContext"),
        }
    if method in {"item/fileChange/requestApproval", "applyPatchApproval"}:
        paths = params.get("fileChanges")
        return {
            "kind": "file",
            "paths": list(paths.keys()) if isinstance(paths, dict) else [],
            "reason": params.get("reason"),
        }
    if method == "item/permissions/requestApproval":
        return {
            "kind": "permissions",
            "permissions": params.get("permissions") or {},
            "reason": params.get("reason"),
        }
    if method in {"item/skill/requestApproval", "skillApproval"}:
        return {
            "kind": "skill",
            "skill": str(params.get("skill") or params.get("skillName") or params.get("name") or ""),
            "reason": params.get("reason"),
        }
    return {"kind": "unknown"}


def approval_response(
    method: str,
    decision: str,
    params: dict[str, Any] | None = None,
) -> Any:
    params = params or {}
    if method in {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
        "item/skill/requestApproval",
        "skillApproval",
    }:
        return {"decision": decision}
    if method == "item/permissions/requestApproval":
        permissions = params.get("permissions") or {} if decision in {"accept", "acceptForSession"} else {}
        return {"permissions": permissions, "scope": "turn"}
    if method in {"execCommandApproval", "applyPatchApproval"}:
        if decision in {"accept", "acceptForSession"}:
            return {"decision": "approved" if decision == "accept" else "approved_for_session"}
        if decision == "cancel":
            return {"decision": "abort"}
        return {"decision": {"denied": {"rejection": "Declined in Video Fission"}}}
    return {"decision": decision}


def question_list(params: dict[str, Any]) -> list[dict[str, Any]]:
    raw = params.get("questions")
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if str(params.get("requestedSchema", "")).strip():
        requested = params.get("requestedSchema")
        if isinstance(requested, dict):
            properties = requested.get("properties")
            required = set(requested.get("required") or [])
            if isinstance(properties, dict):
                return [
                    {
                        "id": str(name),
                        "header": str((value or {}).get("title") or name),
                        "question": str((value or {}).get("description") or (value or {}).get("title") or name),
                        "required": name in required,
                        "type": str((value or {}).get("type") or "string"),
                        "options": (value or {}).get("enum") or [],
                    }
                    for name, value in properties.items()
                ]
    return []
