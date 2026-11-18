from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .. import container
from ..auth import get_current_user, owner_id
from ..paths import user_upload_root
from ..productions import ProductionConflict, ProductionNotFound
from ..service import ConversationBusy, ConversationNotFound, RuntimeUnavailable
from ..skills import SkillValidationError

router = APIRouter(prefix="/api", dependencies=[Depends(get_current_user)])
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
    model_config = ConfigDict(extra="ignore")
    text: str = Field(default="", max_length=30_000)
    mentions: list[Mention] = Field(default_factory=list, max_length=20)
    files: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    replace_policy: dict[str, Any] | None = None


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
    if isinstance(exc, KeyError):
        raise HTTPException(404, "资源不存在") from exc
    if isinstance(exc, ConversationNotFound):
        raise HTTPException(404, str(exc)) from exc
    if isinstance(exc, ConversationBusy):
        raise HTTPException(409, str(exc)) from exc
    if isinstance(exc, RuntimeUnavailable):
        raise HTTPException(503, str(exc)) from exc
    if isinstance(exc, (ValueError, SkillValidationError)):
        raise HTTPException(400, str(exc)) from exc
    raise exc


def _conversation_owner(conversation_id: str, current_user: dict[str, Any]) -> None:
    if not container.db.get_conversation(conversation_id, owner_id(current_user)):
        raise HTTPException(404, "对话不存在")


@router.get("/runtime")
def runtime() -> dict[str, Any]:
    return container.hub.runtime_status()


@router.get("/runtime/skills")
def runtime_skills() -> dict[str, Any]:
    try:
        return container.hub.runtime_skills()
    except Exception as exc:
        _error(exc)


@router.get("/capabilities")
def capabilities() -> dict[str, Any]:
    return container.hub.capabilities()


@router.get("/models")
def models(refresh: bool = False) -> dict[str, Any]:
    try:
        rows = container.hub.list_models(refresh=refresh)
    except Exception as exc:
        _error(exc)
    return {"items": rows}


@router.get("/conversations")
def conversations(
    include_archived: bool = False,
    archived: bool | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=512),
    q: str | None = Query(default=None, max_length=120),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return container.hub.list_conversations(
            include_archived=include_archived,
            user_id=owner_id(current_user),
            archived=archived,
            limit=limit,
            cursor=cursor,
            query=q,
        )
    except Exception as exc:
        _error(exc)


@router.post("/conversations", status_code=201)
def create_conversation(
    request: ConversationCreate | None = None,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    return container.hub.create_conversation(
        request.model_dump(exclude_none=True) if request else {},
        user_id=owner_id(current_user),
    )


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return container.hub.get_conversation(conversation_id, user_id=owner_id(current_user))
    except Exception as exc:
        _error(exc)


@router.patch("/conversations/{conversation_id}")
def update_conversation(
    conversation_id: str,
    request: ConversationUpdate,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return container.hub.update_conversation(
            conversation_id,
            request.model_dump(exclude_unset=True),
            user_id=owner_id(current_user),
        )
    except Exception as exc:
        _error(exc)


@router.delete("/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, bool]:
    try:
        container.hub.delete(conversation_id, user_id=owner_id(current_user))
        return {"deleted": True}
    except Exception as exc:
        _error(exc)


@router.post("/conversations/{conversation_id}/messages")
def send_message(
    conversation_id: str,
    request: MessageRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return container.hub.send_message(
            conversation_id,
            request.model_dump(exclude_none=True),
            user_id=owner_id(current_user),
        )
    except Exception as exc:
        _error(exc)


@router.post("/conversations/{conversation_id}/steer")
def steer(
    conversation_id: str,
    request: MessageRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return container.hub.steer(
            conversation_id,
            request.model_dump(exclude_none=True),
            user_id=owner_id(current_user),
        )
    except Exception as exc:
        _error(exc)


@router.post("/conversations/{conversation_id}/interrupt")
def interrupt(
    conversation_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return container.hub.interrupt(conversation_id, user_id=owner_id(current_user))
    except Exception as exc:
        _error(exc)


@router.post("/conversations/{conversation_id}/compact")
def compact(
    conversation_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return container.hub.compact(conversation_id, user_id=owner_id(current_user))
    except Exception as exc:
        _error(exc)


@router.post("/conversations/{conversation_id}/fork", status_code=201)
def fork(
    conversation_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return container.hub.fork(conversation_id, user_id=owner_id(current_user))
    except Exception as exc:
        _error(exc)


@router.post("/conversations/{conversation_id}/archive")
def archive(
    conversation_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return container.hub.archive(conversation_id, True, user_id=owner_id(current_user))
    except Exception as exc:
        _error(exc)


@router.post("/conversations/{conversation_id}/unarchive")
def unarchive(
    conversation_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return container.hub.archive(conversation_id, False, user_id=owner_id(current_user))
    except Exception as exc:
        _error(exc)


@router.post("/conversations/{conversation_id}/sync")
def sync(
    conversation_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return container.hub.sync(conversation_id, user_id=owner_id(current_user))
    except Exception as exc:
        _error(exc)


@router.get("/conversations/{conversation_id}/events")
async def events(
    conversation_id: str,
    request: Request,
    after_id: int = Query(default=0, ge=0),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> StreamingResponse:
    try:
        container.hub.get_conversation(conversation_id, user_id=owner_id(current_user))
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
            rows = container.hub.db.list_events(
                conversation_id,
                after_id=cursor,
                limit=250,
                user_id=owner_id(current_user),
            )
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
def decide_approval(
    approval_id: str,
    request: ApprovalDecision,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    approval = container.db.get_approval(approval_id)
    if not approval:
        raise HTTPException(404, "审批请求不存在")
    _conversation_owner(str(approval["conversation_id"]), current_user)
    try:
        return container.hub.decide_approval(approval_id, request.decision)
    except Exception as exc:
        _error(exc)


@router.post("/clarifications/{clarification_id}/answer")
def answer_clarification(
    clarification_id: str,
    request: ClarificationAnswers,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    clarification = container.db.get_clarification(clarification_id)
    if not clarification:
        raise HTTPException(404, "澄清请求不存在")
    _conversation_owner(str(clarification["conversation_id"]), current_user)
    try:
        return container.hub.answer_clarification(clarification_id, request.answers)
    except Exception as exc:
        _error(exc)


@router.post("/uploads", status_code=201)
async def upload(
    file: UploadFile = File(...),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
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
    upload_root = user_upload_root(container.settings.uploads_dir, owner_id(current_user))
    upload_root.mkdir(parents=True, exist_ok=True)
    stored = upload_root / f"{uuid.uuid4().hex}{suffix}"
    stored.write_bytes(b"".join(chunks))
    return {
        "id": stored.stem,
        "name": original,
        "path": str(stored.resolve()),
        "size": total,
        "content_type": file.content_type or "application/octet-stream",
    }


@router.delete("/uploads/{file_id}")
def delete_upload(
    file_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, bool]:
    if not re.fullmatch(r"[a-f0-9]{32}", file_id):
        raise HTTPException(400, "附件标识无效")
    upload_root = user_upload_root(container.settings.uploads_dir, owner_id(current_user))
    matches = [path for path in upload_root.glob(f"{file_id}.*") if path.is_file()]
    if not matches and current_user.get("role") == "admin":
        matches = [
            path
            for path in container.settings.uploads_dir.glob(f"{file_id}.*")
            if path.is_file()
        ]
    if not matches:
        raise HTTPException(404, "附件不存在")
    if any(container.hub.db.attachment_reference_count(str(path.resolve())) for path in matches):
        raise HTTPException(409, "文件已被对话使用，不能单独删除")
    for path in matches:
        path.unlink(missing_ok=True)
    return {"deleted": True}


@router.get("/skills")
def list_skills() -> dict[str, Any]:
    return {"items": container.skills.list(), "roots": container.skills.runtime_roots}


@router.get("/skills/{slug}")
def get_skill(slug: str) -> dict[str, Any]:
    try:
        return container.skills.get(slug)
    except KeyError as exc:
        raise HTTPException(404, "Skill 不存在") from exc


@router.post("/skills", status_code=201)
def create_skill(request: SkillCreate) -> dict[str, Any]:
    try:
        return container.skills.create(request.model_dump())
    except Exception as exc:
        _error(exc)


@router.put("/skills/{slug}")
def update_skill(slug: str, request: SkillUpdate) -> dict[str, Any]:
    try:
        return container.skills.update(slug, request.model_dump(exclude_unset=True))
    except Exception as exc:
        _error(exc)


@router.post("/skills/{slug}/toggle")
def toggle_skill(slug: str, request: SkillToggle) -> dict[str, Any]:
    try:
        return container.skills.toggle(slug, request.enabled)
    except Exception as exc:
        _error(exc)


@router.delete("/skills/{slug}")
def delete_skill(slug: str) -> dict[str, bool]:
    try:
        container.skills.delete(slug)
        return {"deleted": True}
    except Exception as exc:
        _error(exc)


@router.get("/tools")
def list_tools() -> dict[str, Any]:
    return {"items": container.hub.tools.summary()}


@router.get("/tool-runs")
def list_tool_runs(
    conversation_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    _conversation_owner(conversation_id, current_user)
    return {"items": container.hub.db.list_tool_runs(conversation_id)}


class SettingsTestRequest(BaseModel):
    target: str = Field(pattern="^(multimodal|video_generator|tts)$")


class TimeRangeBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    start_ms: int
    end_ms: int


class ReplacePolicyBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    mode: str | None = "default"
    roles: list[str] = Field(default_factory=list)
    segment_ids: list[str] = Field(default_factory=list)
    time_ranges: list[TimeRangeBody] = Field(default_factory=list)


class ProductionCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str | None = Field(default=None, max_length=120)
    brief: str = Field(min_length=1, max_length=20_000)
    video_upload_id: str = Field(min_length=1, max_length=80)
    conversation_id: str | None = Field(default=None, max_length=80)
    replace_policy: ReplacePolicyBody | None = None


class ProductionPatch(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str | None = Field(default=None, max_length=120)
    brief: str | None = Field(default=None, min_length=1, max_length=20_000)
    conversation_id: str | None = Field(default=None, max_length=80)
    replace_policy: ReplacePolicyBody | None = None


class ProductionRunBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    replace_policy: ReplacePolicyBody | None = None
    resume_from: str | None = None


def _production_error(exc: Exception) -> None:
    if isinstance(exc, ProductionNotFound):
        raise HTTPException(404, str(exc)) from exc
    if isinstance(exc, ProductionConflict):
        raise HTTPException(409, str(exc)) from exc
    if isinstance(exc, FileNotFoundError):
        raise HTTPException(404, str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(400, str(exc)) from exc
    raise exc


@router.get("/settings")
def get_settings(current_user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return container.settings_store.public_settings(user_id=owner_id(current_user))


@router.put("/settings")
async def put_settings(
    request: Request,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(400, "请求体必须是 JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "请求体必须是对象")
    try:
        return container.settings_store.update(payload, user_id=owner_id(current_user))
    except Exception as exc:
        _error(exc)


@router.post("/settings/test")
def test_settings(
    body: SettingsTestRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return container.settings_store.test_target(
            body.target,
            user_id=owner_id(current_user),
        )
    except Exception as exc:
        _error(exc)


@router.post("/productions/uploads", status_code=201)
async def upload_production_video(
    file: UploadFile = File(...),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    async def chunks():
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            yield chunk

    # Materialize in a sync iterator for the service.
    collected = []
    total = 0
    async for chunk in chunks():
        collected.append(chunk)
        total += len(chunk)
        if total > 500 * 1024 * 1024:
            raise HTTPException(413, "文件不能超过 500MB")
    try:
        return container.productions.save_upload(
            file.filename or "reference.mp4",
            file.content_type or "application/octet-stream",
            collected,
            user_id=owner_id(current_user),
        )
    except Exception as exc:
        _production_error(exc)


@router.get("/productions/uploads/{upload_id}")
def get_production_upload(
    upload_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    try:
        path, record = container.productions.upload_media_path(
            upload_id,
            user_id=owner_id(current_user),
        )
    except Exception as exc:
        _production_error(exc)
    return FileResponse(
        path,
        media_type=str(record.get("content_type") or "video/mp4"),
        filename=record.get("name") or path.name,
        content_disposition_type="inline",
    )


@router.post("/productions", status_code=201)
def create_production(
    body: ProductionCreate,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        policy = body.replace_policy.model_dump() if body.replace_policy is not None else None
        return container.productions.create(
            body.title,
            body.brief,
            body.video_upload_id,
            replace_policy=policy,
            conversation_id=body.conversation_id,
            user_id=owner_id(current_user),
        )
    except Exception as exc:
        _production_error(exc)


@router.get("/productions")
def list_productions(
    conversation_id: str | None = Query(default=None),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    filtered = conversation_id.strip() if conversation_id else None
    if filtered:
        _conversation_owner(filtered, current_user)
    return {
        "items": container.productions.list_items(
            conversation_id=filtered,
            user_id=owner_id(current_user),
        )
    }


@router.get("/productions/{production_id}")
def get_production(
    production_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return container.productions.get(production_id, user_id=owner_id(current_user))
    except Exception as exc:
        _production_error(exc)


@router.patch("/productions/{production_id}")
def patch_production(
    production_id: str,
    body: ProductionPatch,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        policy = body.replace_policy.model_dump() if body.replace_policy is not None else None
        return container.productions.patch(
            production_id,
            title=body.title,
            brief=body.brief,
            replace_policy=policy,
            conversation_id=body.conversation_id,
            user_id=owner_id(current_user),
        )
    except Exception as exc:
        _production_error(exc)


@router.post("/productions/{production_id}/run")
async def run_production(
    production_id: str,
    request: Request,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    policy = None
    resume_from = None
    raw = await request.body()
    if raw.strip():
        try:
            body = ProductionRunBody.model_validate_json(raw)
        except Exception as exc:
            raise HTTPException(400, f"run 请求体无效: {exc}") from exc
        policy = body.replace_policy.model_dump() if body.replace_policy is not None else None
        resume_from = body.resume_from
    try:
        return container.productions.run(
            production_id,
            replace_policy=policy,
            resume_from=resume_from,
            user_id=owner_id(current_user),
        )
    except Exception as exc:
        _production_error(exc)


@router.post("/productions/{production_id}/cancel")
def cancel_production(
    production_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return container.productions.cancel(production_id, user_id=owner_id(current_user))
    except Exception as exc:
        _production_error(exc)


@router.delete("/productions/{production_id}")
def delete_production(
    production_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, bool]:
    try:
        container.productions.delete(production_id, user_id=owner_id(current_user))
        return {"deleted": True}
    except Exception as exc:
        _production_error(exc)


@router.get("/productions/{production_id}/events")
async def production_events(
    production_id: str,
    request: Request,
    after_id: int = Query(default=0, ge=0),
    current_user: dict[str, Any] = Depends(get_current_user),
):
    try:
        container.productions.get(production_id, user_id=owner_id(current_user))
    except Exception as exc:
        _production_error(exc)

    async def stream():
        cursor = after_id
        last_ping = time.monotonic()
        while True:
            if await request.is_disconnected():
                break
            rows = container.productions.events(
                production_id,
                after_id=cursor,
                user_id=owner_id(current_user),
            )
            for row in rows:
                cursor = int(row["id"])
                data = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
                yield f"id: {cursor}\ndata: {data}\n\n"
                last_ping = time.monotonic()
            production = container.db.get_production(production_id, owner_id(current_user))
            if production and production["status"] in {"completed", "failed", "cancelled"}:
                # flush any remaining then stop after a short idle
                if not rows:
                    break
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


@router.get("/productions/{production_id}/artifacts/{name:path}")
def get_production_artifact(
    production_id: str,
    name: str,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    try:
        path = container.productions.artifact_path(
            production_id,
            name,
            user_id=owner_id(current_user),
        )
    except Exception as exc:
        _production_error(exc)
    media_types = {
        ".mp4": "video/mp4",
        ".srt": "application/x-subrip",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".m4a": "audio/mp4",
    }
    return FileResponse(
        path,
        media_type=media_types.get(path.suffix.lower(), "application/octet-stream"),
        filename=path.name,
    )
