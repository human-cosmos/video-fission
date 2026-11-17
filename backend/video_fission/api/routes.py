from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from ..config import settings
from ..container import hub, skills
from ..service import ConversationBusy, ConversationNotFound, RuntimeUnavailable
from ..skills import SkillValidationError

router = APIRouter(prefix="/api")
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    model: str | None = None
    reasoning_effort: str | None = None
    developer_instructions: str | None = Field(default=None, max_length=20_000)


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    model: str | None = None
    reasoning_effort: str | None = None
    developer_instructions: str | None = Field(default=None, max_length=20_000)


class Mention(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    path: str = Field(min_length=1, max_length=500)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized.startswith(("app://", "plugin://", "skill://")):
            raise ValueError("mention 只支持 app://、plugin:// 或 skill:// 目标")
        return value.strip()


class MessageRequest(BaseModel):
    text: str = Field(default="", max_length=30_000)
    mentions: list[Mention] = Field(default_factory=list, max_length=20)
    files: list[dict[str, Any]] = Field(default_factory=list, max_length=20)


class ApprovalDecision(BaseModel):
    decision: str = Field(pattern="^(accept|acceptForSession|decline|cancel)$")


class ClarificationAnswers(BaseModel):
    answers: dict[str, Any]


class SkillCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=200_000)
    slug: str | None = Field(default=None, max_length=64, pattern=r"^[a-zA-Z0-9-]*$")


class SkillUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, min_length=1, max_length=500)
    body: str | None = Field(default=None, min_length=1, max_length=200_000)


class SkillToggle(BaseModel):
    enabled: bool


def _error(exc: Exception) -> None:
    if isinstance(exc, ConversationNotFound):
        raise HTTPException(404, str(exc)) from exc
    if isinstance(exc, ConversationBusy):
        raise HTTPException(409, str(exc)) from exc
    if isinstance(exc, RuntimeUnavailable):
        raise HTTPException(503, str(exc)) from exc
    if isinstance(exc, (ValueError, SkillValidationError)):
        raise HTTPException(400, str(exc)) from exc
    raise exc


@router.get("/health")
def health() -> dict[str, Any]:
    runtime = hub.runtime_status()
    return {
        "ok": True,
        "service": "video-fission",
        "runtime": runtime,
        "database": str(settings.database_path),
    }


@router.get("/runtime")
def runtime() -> dict[str, Any]:
    return hub.runtime_status()


@router.get("/runtime/skills")
def runtime_skills() -> dict[str, Any]:
    try:
        return hub.runtime_skills()
    except Exception as exc:
        _error(exc)


@router.get("/capabilities")
def capabilities() -> dict[str, Any]:
    return hub.capabilities()


@router.get("/models")
def models(refresh: bool = False) -> dict[str, Any]:
    try:
        rows = hub.list_models(refresh=refresh)
    except Exception as exc:
        _error(exc)
    return {"items": rows}


@router.get("/conversations")
def conversations(include_archived: bool = False) -> dict[str, Any]:
    return {"items": hub.list_conversations(include_archived=include_archived)}


@router.post("/conversations", status_code=201)
def create_conversation(request: ConversationCreate | None = None) -> dict[str, Any]:
    return hub.create_conversation(request.model_dump(exclude_none=True) if request else {})


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str) -> dict[str, Any]:
    try:
        return hub.get_conversation(conversation_id)
    except Exception as exc:
        _error(exc)


@router.patch("/conversations/{conversation_id}")
def update_conversation(conversation_id: str, request: ConversationUpdate) -> dict[str, Any]:
    try:
        return hub.update_conversation(conversation_id, request.model_dump(exclude_unset=True))
    except Exception as exc:
        _error(exc)


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: str) -> dict[str, bool]:
    try:
        hub.delete(conversation_id)
        return {"deleted": True}
    except Exception as exc:
        _error(exc)


@router.post("/conversations/{conversation_id}/messages")
def send_message(conversation_id: str, request: MessageRequest) -> dict[str, Any]:
    try:
        return hub.send_message(conversation_id, request.model_dump(exclude_none=True))
    except Exception as exc:
        _error(exc)


@router.post("/conversations/{conversation_id}/steer")
def steer(conversation_id: str, request: MessageRequest) -> dict[str, Any]:
    try:
        return hub.steer(conversation_id, request.model_dump(exclude_none=True))
    except Exception as exc:
        _error(exc)


@router.post("/conversations/{conversation_id}/interrupt")
def interrupt(conversation_id: str) -> dict[str, Any]:
    try:
        return hub.interrupt(conversation_id)
    except Exception as exc:
        _error(exc)


@router.post("/conversations/{conversation_id}/compact")
def compact(conversation_id: str) -> dict[str, Any]:
    try:
        return hub.compact(conversation_id)
    except Exception as exc:
        _error(exc)


@router.post("/conversations/{conversation_id}/fork", status_code=201)
def fork(conversation_id: str) -> dict[str, Any]:
    try:
        return hub.fork(conversation_id)
    except Exception as exc:
        _error(exc)


@router.post("/conversations/{conversation_id}/archive")
def archive(conversation_id: str) -> dict[str, Any]:
    try:
        return hub.archive(conversation_id, True)
    except Exception as exc:
        _error(exc)


@router.post("/conversations/{conversation_id}/unarchive")
def unarchive(conversation_id: str) -> dict[str, Any]:
    try:
        return hub.archive(conversation_id, False)
    except Exception as exc:
        _error(exc)


@router.post("/conversations/{conversation_id}/sync")
def sync(conversation_id: str) -> dict[str, Any]:
    try:
        return hub.sync(conversation_id)
    except Exception as exc:
        _error(exc)


@router.get("/conversations/{conversation_id}/events")
async def events(
    conversation_id: str,
    request: Request,
    after_id: int = Query(default=0, ge=0),
) -> StreamingResponse:
    try:
        hub.get_conversation(conversation_id)
    except Exception as exc:
        _error(exc)

    last_event_id = request.headers.get("last-event-id", "").strip()
    resume_id = max(after_id, int(last_event_id)) if last_event_id.isdigit() else after_id

    async def stream():
        cursor = resume_id
        last_ping = time.monotonic()
        while True:
            if await request.is_disconnected():
                break
            rows = hub.db.list_events(conversation_id, after_id=cursor, limit=250)
            for row in rows:
                cursor = int(row["id"])
                data = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
                yield f"id: {cursor}\ndata: {data}\n\n"
                last_ping = time.monotonic()
            if time.monotonic() - last_ping >= 12:
                yield f": heartbeat {int(time.time())}\n\n"
                last_ping = time.monotonic()
            await asyncio.sleep(0.45)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/approvals/{approval_id}/decide")
def decide_approval(approval_id: str, request: ApprovalDecision) -> dict[str, Any]:
    try:
        return hub.decide_approval(approval_id, request.decision)
    except Exception as exc:
        _error(exc)


@router.post("/clarifications/{clarification_id}/answer")
def answer_clarification(clarification_id: str, request: ClarificationAnswers) -> dict[str, Any]:
    try:
        return hub.answer_clarification(clarification_id, request.answers)
    except Exception as exc:
        _error(exc)


@router.post("/uploads", status_code=201)
async def upload(file: UploadFile = File(...)) -> dict[str, Any]:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "文件不能超过 20MB")
        chunks.append(chunk)
    original = Path(file.filename or "attachment").name
    suffix = Path(original).suffix[:12]
    stored = settings.uploads_dir / f"{uuid.uuid4().hex}{suffix}"
    stored.write_bytes(b"".join(chunks))
    return {
        "id": stored.stem,
        "name": original,
        "path": str(stored.resolve()),
        "size": total,
        "content_type": file.content_type or "application/octet-stream",
    }


@router.delete("/uploads/{file_id}")
def delete_upload(file_id: str) -> dict[str, bool]:
    if not re.fullmatch(r"[a-f0-9]{32}", file_id):
        raise HTTPException(400, "附件标识无效")
    matches = [path for path in settings.uploads_dir.glob(f"{file_id}.*") if path.is_file()]
    if not matches:
        raise HTTPException(404, "附件不存在")
    if any(hub.db.attachment_reference_count(str(path.resolve())) for path in matches):
        raise HTTPException(409, "文件已被对话使用，不能单独删除")
    for path in matches:
        path.unlink(missing_ok=True)
    return {"deleted": True}


@router.get("/skills")
def list_skills() -> dict[str, Any]:
    return {"items": skills.list(), "roots": skills.runtime_roots}


@router.get("/skills/{slug}")
def get_skill(slug: str) -> dict[str, Any]:
    try:
        return skills.get(slug)
    except KeyError as exc:
        raise HTTPException(404, "Skill 不存在") from exc


@router.post("/skills", status_code=201)
def create_skill(request: SkillCreate) -> dict[str, Any]:
    try:
        return skills.create(request.model_dump())
    except Exception as exc:
        _error(exc)


@router.put("/skills/{slug}")
def update_skill(slug: str, request: SkillUpdate) -> dict[str, Any]:
    try:
        return skills.update(slug, request.model_dump(exclude_unset=True))
    except Exception as exc:
        _error(exc)


@router.post("/skills/{slug}/toggle")
def toggle_skill(slug: str, request: SkillToggle) -> dict[str, Any]:
    try:
        return skills.toggle(slug, request.enabled)
    except Exception as exc:
        _error(exc)


@router.delete("/skills/{slug}")
def delete_skill(slug: str) -> dict[str, bool]:
    try:
        skills.delete(slug)
        return {"deleted": True}
    except Exception as exc:
        _error(exc)


@router.get("/tools")
def list_tools() -> dict[str, Any]:
    return {"items": hub.tools.summary()}


@router.get("/tool-runs")
def list_tool_runs(conversation_id: str) -> dict[str, Any]:
    return {"items": hub.db.list_tool_runs(conversation_id)}
