from __future__ import annotations

import logging
import sqlite3
from typing import Any
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field

from .. import container, media
from ..auth import (
    check_login_rate_limit,
    clear_login_cookie,
    clear_login_failures,
    create_access_token,
    get_current_user,
    hash_password,
    public_user,
    record_login_failure,
    require_admin,
    set_login_cookie,
    validate_username,
    verify_password,
)
from ..sso import sso_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["auth"])


@router.get("/health", include_in_schema=False)
def health() -> dict[str, Any]:
    runtime_status = container.hub.runtime_status()
    runtime = {
        key: runtime_status[key]
        for key in ("available", "version", "warning", "online_conversations")
        if key in runtime_status
    }
    ffmpeg = media.ffmpeg_available()
    ffmpeg_public = {
        key: ffmpeg[key]
        for key in ("ok", "version", "detail")
        if key in ffmpeg
    }
    return {
        "ok": True,
        "service": "video-fission",
        "runtime": runtime,
        "database": container.settings.database_path.name,
        "ffmpeg": ffmpeg_public,
        "pipeline": {
            "ok": bool(ffmpeg_public.get("ok")),
            "default_provider": "local_ffmpeg",
        },
    }


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8, max_length=256)
    display_name: str = Field(default="", max_length=120)
    role: str = Field(default="user", pattern="^(user|admin)$")


class UserUpdateRequest(BaseModel):
    username: str | None = Field(default=None, min_length=2, max_length=64)
    password: str | None = Field(default=None, min_length=8, max_length=256)
    display_name: str | None = Field(default=None, max_length=120)
    role: str | None = Field(default=None, pattern="^(user|admin)$")


def _trusted_sso_callback(request: Request) -> str:
    configured = container.settings.sso_callback_url.strip()
    if configured:
        return configured
    trusted_hosts = {"localhost", "127.0.0.1", "::1"}
    frontend_host = urlsplit(container.settings.sso_frontend_url).hostname
    if frontend_host:
        trusted_hosts.add(frontend_host)
    trusted_hosts.update(
        host
        for origin in container.settings.allowed_origins
        if (host := urlsplit(origin).hostname)
    )
    hostname = request.url.hostname or ""
    if hostname not in trusted_hosts:
        raise ValueError("SSO 回调地址必须通过 VF_SSO_CALLBACK_URL 显式配置")
    return str(request.url_for("sso_callback"))


@router.post("/auth/login")
def login(
    request: Request,
    response: Response,
    form: OAuth2PasswordRequestForm = Depends(),
) -> dict[str, Any]:
    client_ip = request.client.host if request.client else "unknown"
    check_login_rate_limit(client_ip)
    user = container.db.get_user_by_username(form.username)
    if not user or not verify_password(form.password, str(user.get("password_hash") or "")):
        record_login_failure(client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    clear_login_failures(client_ip)
    token = create_access_token(user)
    set_login_cookie(response, request, token)
    return {"access_token": token, "token_type": "bearer", "user": public_user(user)}


@router.get("/auth/me")
def me(current_user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return public_user(current_user) or {}


@router.post("/auth/logout")
def logout(request: Request) -> Response:
    response = Response(content='{"detail":"已退出登录"}', media_type="application/json")
    clear_login_cookie(response, request)
    return response


@router.get("/sso/login")
def sso_login(request: Request) -> dict[str, str]:
    try:
        callback = _trusted_sso_callback(request)
        return {"auth_url": sso_service.build_auth_url(callback)}
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/sso/callback", name="sso_callback")
def sso_callback(request: Request, ticket: str | None = None) -> RedirectResponse:
    frontend_url = container.settings.sso_frontend_url.rstrip("/") or str(request.base_url).rstrip("/")
    if not ticket:
        return RedirectResponse(
            f"{frontend_url}/?sso_error={quote('缺少 ticket 参数')}",
            status_code=status.HTTP_302_FOUND,
        )
    try:
        result = sso_service.verify_and_login(ticket)
        response = RedirectResponse(
            f"{frontend_url}/?sso=success",
            status_code=status.HTTP_302_FOUND,
        )
        set_login_cookie(response, request, result["access_token"])
        return response
    except Exception as exc:
        logger.exception("SSO callback failed")
        message = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "SSO 登录失败，请稍后重试"
        return RedirectResponse(
            f"{frontend_url}/?sso_error={quote(message[:200])}",
            status_code=status.HTTP_302_FOUND,
        )


@router.get("/users")
def list_users(_: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    return {"items": [public_user(user) for user in container.db.list_users()]}


@router.post("/users", status_code=201)
def create_user(
    body: UserCreateRequest,
    _: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    try:
        username = validate_username(body.username)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if container.db.get_user_by_username(username):
        raise HTTPException(409, "用户名已存在")
    try:
        user = container.db.create_user(
            username=username,
            password_hash=hash_password(body.password),
            role=body.role,
            display_name=body.display_name,
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "用户名已存在") from exc
    return public_user(user) or {}


@router.patch("/users/{user_id}")
def update_user(
    user_id: str,
    body: UserUpdateRequest,
    current_admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    user = container.db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(404, "用户不存在")
    username = None
    if body.username is not None:
        try:
            username = validate_username(body.username)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        duplicate = container.db.get_user_by_username(username)
        if duplicate and duplicate["id"] != user_id:
            raise HTTPException(409, "用户名已存在")
    if user_id == current_admin.get("id") and body.role == "user":
        raise HTTPException(400, "不能移除自己的管理员权限")
    revoke_tokens = bool(body.password) or (
        body.role is not None and body.role != user.get("role")
    )
    try:
        updated = container.db.update_user(
            user_id,
            username=username,
            password_hash=hash_password(body.password) if body.password else None,
            display_name=body.display_name,
            role=body.role,
            revoke_tokens=revoke_tokens,
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "用户名已存在") from exc
    return public_user(updated) or {}


@router.delete("/users/{user_id}")
def delete_user(
    user_id: str,
    current_admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, bool]:
    if user_id == current_admin.get("id"):
        raise HTTPException(400, "不能删除当前登录用户")
    if not container.db.get_user_by_id(user_id):
        raise HTTPException(404, "用户不存在")
    container.db.delete_user(user_id)
    return {"deleted": True}
