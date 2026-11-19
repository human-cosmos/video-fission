from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .. import container
from ..auth import get_current_user, owner_id
from ..productions import ProductionConflict, ProductionNotFound

router = APIRouter(prefix="/api/remixes", dependencies=[Depends(get_current_user)])


class RemixCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    video_upload_id: str = Field(min_length=1, max_length=80)
    title: str | None = Field(default=None, max_length=120)


class RemixRender(BaseModel):
    model_config = ConfigDict(extra="ignore")
    opening_end_ms: int | None = Field(default=None, ge=1)
    ending_start_ms: int | None = Field(default=None, ge=1)
    middle_order: list[str] | None = Field(default=None, max_length=64)
    strategy: str | None = Field(default=None, max_length=32)
    seed: str | None = Field(default=None, max_length=120)


def _remix_error(exc: Exception) -> None:
    if isinstance(exc, ProductionNotFound):
        raise HTTPException(404, str(exc)) from exc
    if isinstance(exc, ProductionConflict):
        raise HTTPException(409, str(exc)) from exc
    if isinstance(exc, FileNotFoundError):
        raise HTTPException(404, str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(400, str(exc)) from exc
    raise exc


@router.post("", status_code=201)
def create_remix(
    body: RemixCreate,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return container.remixes.create(
            body.video_upload_id,
            title=body.title,
            user_id=owner_id(current_user),
        )
    except Exception as exc:
        _remix_error(exc)


@router.get("/{remix_id}")
def get_remix(
    remix_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return container.remixes.public(remix_id, user_id=owner_id(current_user))
    except Exception as exc:
        _remix_error(exc)


@router.post("/{remix_id}/render")
def render_remix(
    remix_id: str,
    body: RemixRender,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return container.remixes.render(
            remix_id,
            opening_end_ms=body.opening_end_ms,
            ending_start_ms=body.ending_start_ms,
            middle_order=body.middle_order,
            strategy=body.strategy,
            seed=body.seed,
            user_id=owner_id(current_user),
        )
    except Exception as exc:
        _remix_error(exc)


@router.post("/{remix_id}/cancel")
def cancel_remix(
    remix_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return container.remixes.cancel(remix_id, user_id=owner_id(current_user))
    except Exception as exc:
        _remix_error(exc)


@router.delete("/{remix_id}")
def delete_remix(
    remix_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, bool]:
    try:
        container.remixes.delete(remix_id, user_id=owner_id(current_user))
        return {"deleted": True}
    except Exception as exc:
        _remix_error(exc)


@router.get("/{remix_id}/events")
async def remix_events(
    remix_id: str,
    request: Request,
    after_id: int = Query(default=0, ge=0),
    current_user: dict[str, Any] = Depends(get_current_user),
):
    user_id = owner_id(current_user)
    try:
        container.remixes.get(remix_id, user_id=user_id)
    except Exception as exc:
        _remix_error(exc)

    async def stream():
        cursor = after_id
        last_ping = time.monotonic()
        while True:
            if await request.is_disconnected():
                break
            rows = container.remixes.events(
                remix_id,
                after_id=cursor,
                user_id=user_id,
            )
            for row in rows:
                cursor = int(row["id"])
                yield f"id: {cursor}\ndata: {json.dumps(row, ensure_ascii=False, separators=(',', ':'))}\n\n"
                last_ping = time.monotonic()
            production = container.db.get_production(
                remix_id,
                user_id,
                task_kind="reference_remix",
            )
            if production and production["status"] in {"completed", "failed", "cancelled"} and not rows:
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


@router.get("/{remix_id}/artifacts/{name:path}")
def get_remix_artifact(
    remix_id: str,
    name: str,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    try:
        path = container.remixes.artifact_path(
            remix_id,
            name,
            user_id=owner_id(current_user),
        )
    except Exception as exc:
        _remix_error(exc)
    media_types = {
        ".mp4": "video/mp4",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
    }
    return FileResponse(
        path,
        media_type=media_types.get(path.suffix.lower(), "application/octet-stream"),
        filename=path.name,
    )
