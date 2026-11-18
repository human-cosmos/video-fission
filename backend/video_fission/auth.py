from __future__ import annotations

import logging
import re
import secrets
import sqlite3
import threading
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Depends, HTTPException, Request, Response, status
from jose import JWTError, jwt
from passlib.context import CryptContext

from . import container

logger = logging.getLogger(__name__)

JWT_ALGORITHM = "HS256"
AUTH_COOKIE_NAME = "video_fission_token"
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.@-]{2,64}$")
LOCAL_USER = {"id": None, "username": "local", "display_name": "本地用户", "role": "admin"}

pwd_context = CryptContext(schemes=["bcrypt_sha256", "bcrypt"], deprecated="auto")

_fail_records: dict[str, list[float]] = defaultdict(list)
_fail_lock = threading.Lock()
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW_SECONDS = 300


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return pwd_context.verify(password, password_hash)
    except (TypeError, ValueError):
        return False


def public_user(user: dict[str, Any] | None) -> dict[str, Any] | None:
    if not user:
        return None
    result = {
        "username": user.get("username") or "",
        "display_name": user.get("display_name") or "",
        "role": user.get("role") or "user",
    }
    if user.get("id"):
        result["id"] = user["id"]
    if user.get("sso_user_id") is not None:
        result["sso_user_id"] = user["sso_user_id"]
    if user.get("area_id") is not None:
        result["area_id"] = user["area_id"]
    return result


def owner_id(user: dict[str, Any] | None) -> str | None:
    value = str((user or {}).get("id") or "").strip()
    return value or None


def create_access_token(user: dict[str, Any], *, expires_minutes: int | None = None) -> str:
    minutes = int(expires_minutes or container.settings.auth_token_expire_minutes or 1440)
    now = datetime.now(UTC)
    payload = {
        "sub": str(user.get("id") or user.get("username") or ""),
        "username": user.get("username") or "",
        "ver": int(user.get("token_version") or 0),
        "iat": int(now.timestamp()),
        "exp": now + timedelta(minutes=max(1, minutes)),
        "typ": "access",
    }
    return jwt.encode(
        payload,
        container.settings.auth_secret_key,
        algorithm=JWT_ALGORITHM,
    )


def _request_is_secure(request: Request) -> bool:
    forwarded = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
    return request.url.scheme == "https" or forwarded == "https"


def set_login_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        max_age=max(60, int(container.settings.auth_token_expire_minutes) * 60),
        domain=container.settings.auth_cookie_domain or None,
        path="/",
        httponly=True,
        secure=_request_is_secure(request),
        samesite="lax",
    )


def clear_login_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        key=AUTH_COOKIE_NAME,
        domain=container.settings.auth_cookie_domain or None,
        path="/",
        httponly=True,
        secure=_request_is_secure(request),
        samesite="lax",
    )


def _tokens_from_request(request: Request) -> list[str]:
    tokens: list[str] = []
    authorization = request.headers.get("authorization") or ""
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        tokens.append(value.strip())
    cookie = request.cookies.get(AUTH_COOKIE_NAME) or ""
    if cookie and cookie not in tokens:
        tokens.append(cookie)
    return tokens


def user_from_token(token: str) -> dict[str, Any] | None:
    if not token:
        return None
    try:
        payload = jwt.decode(
            token,
            container.settings.auth_secret_key,
            algorithms=[JWT_ALGORITHM],
        )
    except JWTError:
        return None
    if payload.get("typ") != "access":
        return None
    subject = str(payload.get("sub") or "").strip()
    user = container.db.get_user_by_id(subject)
    if user is None:
        return None
    if int(payload.get("ver") or 0) != int(user.get("token_version") or 0):
        return None
    return user


async def get_current_user(request: Request) -> dict[str, Any]:
    if not container.settings.auth_required:
        return LOCAL_USER
    user = next(
        (candidate for token in _tokens_from_request(request) if (candidate := user_from_token(token)) is not None),
        None,
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录已失效，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    request.state.current_user = user
    return user


async def require_admin(current_user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="该操作仅管理员可用")
    return current_user


def check_login_rate_limit(client_ip: str) -> None:
    now = time.time()
    with _fail_lock:
        if len(_fail_records) > 10_000:
            stale = [
                key
                for key, timestamps in _fail_records.items()
                if not timestamps or now - timestamps[-1] >= _LOGIN_WINDOW_SECONDS
            ]
            for key in stale:
                _fail_records.pop(key, None)
        _fail_records[client_ip] = [
            timestamp for timestamp in _fail_records[client_ip] if now - timestamp < _LOGIN_WINDOW_SECONDS
        ]
        if len(_fail_records[client_ip]) >= _LOGIN_MAX_ATTEMPTS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="登录失败次数过多，请 5 分钟后再试",
            )


def record_login_failure(client_ip: str) -> None:
    with _fail_lock:
        _fail_records[client_ip].append(time.time())


def clear_login_failures(client_ip: str) -> None:
    with _fail_lock:
        _fail_records.pop(client_ip, None)


def bootstrap_auth() -> str | None:
    """Create the first administrator and adopt pre-auth rows once."""
    users = container.db.list_users()
    if not users:
        username = container.settings.auth_admin_username.strip() or "admin"
        password = container.settings.auth_admin_password or secrets.token_urlsafe(18)
        created = True
        try:
            container.db.create_user(
                username=username,
                password_hash=hash_password(password),
                role="admin",
                display_name="管理员",
            )
        except sqlite3.IntegrityError:
            created = False
        if created and not container.settings.auth_admin_password:
            password_file = container.settings.data_dir / "bootstrap_admin.txt"
            password_file.write_text(
                f"username={username}\npassword={password}\n",
                encoding="utf-8",
            )
            password_file.chmod(0o600)
            logger.warning(
                "Created bootstrap administrator %s; password written to %s",
                username,
                password_file,
            )
        users = container.db.list_users()
    administrator = next((user for user in users if user.get("role") == "admin"), None)
    if administrator:
        container.db.assign_legacy_ownership(administrator["id"])
        container.db.adopt_legacy_app_settings(administrator["id"])
        return str(administrator["id"])
    return None


def validate_username(username: str) -> str:
    value = str(username or "").strip()
    if not USERNAME_RE.fullmatch(value):
        raise ValueError("用户名需为 2-64 位字母、数字或 _ . @ -")
    return value
