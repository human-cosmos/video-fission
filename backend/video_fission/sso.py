from __future__ import annotations

import logging
import secrets
from typing import Any
from urllib.parse import urlencode

import httpx

from . import container
from .auth import create_access_token, hash_password, validate_username

logger = logging.getLogger(__name__)


class SSOService:
    def validate_config(self) -> None:
        settings = container.settings
        missing = [
            name
            for name, value in {
                "VF_SSO_CLIENT_ID": settings.sso_client_id,
                "VF_SSO_CLIENT_SECRET": settings.sso_client_secret,
                "VF_SSO_CLIENT_NAME": settings.sso_client_name,
                "VF_SSO_CAS_AUTH_URL": settings.sso_cas_auth_url,
                "VF_SSO_BUSINESS_API_URL": settings.sso_business_api_url,
            }.items()
            if not value
        ]
        if missing:
            raise ValueError(f"SSO 配置不完整: 缺少 {', '.join(missing)}")

    def build_auth_url(self, callback_url: str) -> str:
        self.validate_config()
        settings = container.settings
        query = urlencode(
            {
                "client": settings.sso_client_id,
                "redirect": callback_url,
            }
        )
        separator = "&" if "?" in settings.sso_cas_auth_url else "?"
        return f"{settings.sso_cas_auth_url}{separator}{query}"

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=15, follow_redirects=True)

    def get_access_token(self) -> str:
        settings = container.settings
        url = f"{settings.sso_business_api_url.rstrip('/')}/oauth2/client_token"
        try:
            with self._client() as client:
                response = client.post(
                    url,
                    data={
                        "client_id": settings.sso_client_id,
                        "client_secret": settings.sso_client_secret,
                    },
                )
                data = response.json()
            if data.get("code") == 200 and not data.get("error"):
                raw = data.get("data")
                token = raw.get("client_token") if isinstance(raw, dict) else ""
                if token:
                    return str(token)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("SSO client token request failed: %s", exc)

        if not settings.sso_access_token_url:
            raise RuntimeError("获取 SSO access_token 失败")
        try:
            with self._client() as client:
                response = client.get(
                    settings.sso_access_token_url,
                    params={"client_name": settings.sso_client_name},
                )
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError(f"获取 SSO access_token 失败: {exc}") from exc
        raw = data.get("data")
        token = raw.get("client_token") if isinstance(raw, dict) else ""
        if not token:
            raise RuntimeError("获取 SSO access_token 失败: 响应中无 client_token")
        return str(token)

    def check_ticket(self, access_token: str, ticket: str) -> str:
        settings = container.settings
        url = f"{settings.sso_business_api_url.rstrip('/')}/guard/sso/checkTicket"
        with self._client() as client:
            response = client.post(
                url,
                data={
                    "client": settings.sso_client_id,
                    "ticket": ticket,
                    "ssoLogoutCall": f"{settings.sso_frontend_url.rstrip('/')}/sso/logoutCall",
                },
                headers={"Authorization": access_token},
            )
            data = response.json()
        if data.get("code") != 200:
            raise RuntimeError(f"Ticket 验证失败: {data.get('msg') or '未知错误'}")
        raw = data.get("data")
        login_id = ""
        if isinstance(raw, dict):
            login_id = str(raw.get("loginId") or raw.get("login_id") or "")
        elif raw:
            login_id = str(raw)
        login_id = login_id or str(data.get("loginId") or data.get("login_id") or "")
        if not login_id:
            raise RuntimeError("Ticket 验证失败: 响应中无 loginId")
        return login_id

    def get_user_data(self, access_token: str, login_id: str) -> dict[str, Any]:
        settings = container.settings
        url = f"{settings.sso_business_api_url.rstrip('/')}/guard/sso/getData"
        with self._client() as client:
            response = client.post(
                url,
                data={"apiType": "user", "loginId": login_id},
                headers={"Authorization": access_token},
            )
            data = response.json()
        if data.get("code") != 200 or not isinstance(data.get("data"), dict):
            raise RuntimeError(f"获取 SSO 用户信息失败: {data.get('msg') or '响应无用户数据'}")
        user = data["data"]
        account = str(user.get("account") or "").strip()
        if not account:
            raise RuntimeError("获取 SSO 用户信息失败: 账号为空")
        try:
            account = validate_username(account)
        except ValueError as exc:
            raise RuntimeError(f"获取 SSO 用户信息失败: {exc}") from exc
        try:
            sso_user_id = int(user["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("获取 SSO 用户信息失败: 企业用户 ID 无效") from exc
        return {
            "id": sso_user_id,
            "name": str(user.get("name") or "").strip(),
            "account": account,
            "areaId": user.get("areaId"),
        }

    def sync_local_user(self, sso_user: dict[str, Any]) -> dict[str, Any]:
        username = str(sso_user["account"]).strip()
        sso_user_id = int(sso_user["id"])
        area_id = sso_user.get("areaId")
        display_name = str(sso_user.get("name") or "").strip()[:120]

        linked = container.db.get_user_by_sso_id(sso_user_id)
        if linked:
            conflict = container.db.get_user_by_username(username)
            if conflict and conflict["id"] != linked["id"]:
                raise RuntimeError("SSO 账号与现有本地用户名冲突")
            return container.db.update_user(
                linked["id"],
                username=username if username != linked.get("username") else None,
                display_name=display_name or linked.get("display_name") or "",
                sso_user_id=sso_user_id,
                area_id=area_id,
            ) or linked

        existing = container.db.get_user_by_username(username)
        if existing:
            raise RuntimeError(
                f"本地账号 {username} 已存在且未绑定当前企业身份，请先由管理员处理"
            )
        return container.db.create_user(
            username=username,
            password_hash=hash_password(secrets.token_urlsafe(32)),
            role="user",
            display_name=display_name,
            sso_user_id=sso_user_id,
            area_id=area_id,
        )

    def verify_and_login(self, ticket: str) -> dict[str, Any]:
        self.validate_config()
        access_token = self.get_access_token()
        login_id = self.check_ticket(access_token, ticket)
        sso_user = self.get_user_data(access_token, login_id)
        user = self.sync_local_user(sso_user)
        return {
            "access_token": create_access_token(user),
            "token_type": "bearer",
            "user": {
                "id": user.get("id"),
                "username": user.get("username"),
                "display_name": user.get("display_name") or "",
                "role": user.get("role") or "user",
                **(
                    {"sso_user_id": user["sso_user_id"]}
                    if user.get("sso_user_id") is not None
                    else {}
                ),
                **({"area_id": user["area_id"]} if user.get("area_id") is not None else {}),
            },
        }


sso_service = SSOService()
