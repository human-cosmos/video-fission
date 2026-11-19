from __future__ import annotations

import os
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _env_path(name: str, default: Path) -> Path:
    raw = os.getenv(name, "").strip()
    return Path(raw).expanduser().resolve() if raw else default.resolve()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _seedance_auth_file() -> Path | None:
    configured = os.getenv("VF_SEEDANCE_AUTH_FILE", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    # `npx dksk auth <key>` writes this machine-local credential file.
    return (Path.home() / ".acore" / "auth.json").resolve()


def _auth_secret(data_dir: Path) -> str:
    configured = os.getenv("VF_AUTH_SECRET_KEY", "").strip()
    if configured:
        return configured
    path = data_dir / "auth_secret"
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except FileNotFoundError:
        pass
    value = secrets.token_hex(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_text(encoding="utf-8").strip() or value
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(value)
    return value


@dataclass(frozen=True)
class Settings:
    project_root: Path
    data_dir: Path
    workspace: Path
    skills_dir: Path
    bundled_skills_dir: Path
    disabled_skills_dir: Path
    uploads_dir: Path
    artifacts_dir: Path
    frontend_dist: Path
    database_path: Path
    codex_bin: str | None
    codex_timeout_seconds: int
    approval_timeout_seconds: int
    host: str
    port: int
    auth_required: bool = True
    auth_secret_key: str = ""
    auth_admin_username: str = "admin"
    auth_admin_password: str = ""
    auth_token_expire_minutes: int = 60 * 24
    auth_cookie_domain: str = ""
    sso_client_id: str = ""
    sso_client_secret: str = ""
    sso_client_name: str = "VideoFission"
    sso_cas_auth_url: str = "https://manage.giikin.com/guard/sso/public/auth"
    sso_business_api_url: str = "https://openapi.giikin.com"
    sso_access_token_url: str = "https://idc-ai.giikin.com/api/common/get_access_token"
    sso_callback_url: str = ""
    sso_frontend_url: str = "http://127.0.0.1:5174"
    allowed_origins: tuple[str, ...] = ()
    gemini_api_url: str = ""
    gemini_upload_url: str = ""
    gemini_token_url: str = ""
    gemini_model: str = ""
    gemini_source: str = ""
    seedance_base_url: str = ""
    seedance_api_key: str = ""
    seedance_model: str = ""
    seedance_auth_file: Path | None = None
    allow_local_copy_fallback: bool = False
    e2e_fake_creative_context: bool = False

    def __post_init__(self) -> None:
        if self.auth_required and not self.auth_secret_key:
            object.__setattr__(self, "auth_secret_key", _auth_secret(self.data_dir))

    @classmethod
    def from_env(cls) -> Settings:
        data_dir = _env_path("VF_DATA_DIR", PROJECT_ROOT / "data")
        workspace = _env_path("VF_WORKSPACE", data_dir / "workspace")
        configured_bin = os.getenv("CODEX_BIN", "").strip()
        codex_bin = shutil.which(configured_bin) or configured_bin if configured_bin else shutil.which("codex")
        return cls(
            project_root=PROJECT_ROOT,
            data_dir=data_dir,
            workspace=workspace,
            skills_dir=data_dir / "skills",
            bundled_skills_dir=PROJECT_ROOT / "skills",
            disabled_skills_dir=data_dir / "skills_disabled",
            uploads_dir=data_dir / "uploads",
            artifacts_dir=data_dir / "artifacts",
            frontend_dist=PROJECT_ROOT / "frontend" / "dist",
            database_path=data_dir / "video_fission.db",
            codex_bin=codex_bin,
            codex_timeout_seconds=int(os.getenv("VF_CODEX_TIMEOUT_SECONDS", "180")),
            approval_timeout_seconds=int(os.getenv("VF_APPROVAL_TIMEOUT_SECONDS", "300")),
            host=os.getenv("VF_HOST", "127.0.0.1"),
            port=int(os.getenv("VF_PORT", "8000")),
            auth_required=_env_bool("VF_AUTH_REQUIRED", True),
            auth_secret_key=_auth_secret(data_dir),
            auth_admin_username=os.getenv("VF_AUTH_ADMIN_USERNAME", "admin").strip() or "admin",
            auth_admin_password=os.getenv("VF_AUTH_ADMIN_PASSWORD", ""),
            auth_token_expire_minutes=int(os.getenv("VF_AUTH_TOKEN_EXPIRE_MINUTES", str(60 * 24))),
            auth_cookie_domain=os.getenv("VF_AUTH_COOKIE_DOMAIN", "").strip(),
            sso_client_id=os.getenv("VF_SSO_CLIENT_ID", "").strip(),
            sso_client_secret=os.getenv("VF_SSO_CLIENT_SECRET", "").strip(),
            sso_client_name=os.getenv("VF_SSO_CLIENT_NAME", "VideoFission").strip() or "VideoFission",
            sso_cas_auth_url=os.getenv(
                "VF_SSO_CAS_AUTH_URL",
                "https://manage.giikin.com/guard/sso/public/auth",
            ).strip(),
            sso_business_api_url=os.getenv(
                "VF_SSO_BUSINESS_API_URL",
                "https://openapi.giikin.com",
            ).strip(),
            sso_access_token_url=os.getenv(
                "VF_SSO_ACCESS_TOKEN_URL",
                "https://idc-ai.giikin.com/api/common/get_access_token",
            ).strip(),
            sso_callback_url=os.getenv("VF_SSO_CALLBACK_URL", "").strip(),
            sso_frontend_url=os.getenv("VF_SSO_FRONTEND_URL", "http://127.0.0.1:5174").strip(),
            allowed_origins=tuple(
                item.strip().rstrip("/") for item in os.getenv("VF_ALLOWED_ORIGINS", "").split(",") if item.strip()
            ),
            gemini_api_url=os.getenv("VF_GEMINI_API_URL", "").strip(),
            gemini_upload_url=os.getenv("VF_GEMINI_UPLOAD_URL", "").strip(),
            gemini_token_url=os.getenv(
                "VF_GEMINI_TOKEN_URL",
                os.getenv("VF_SSO_ACCESS_TOKEN_URL", ""),
            ).strip(),
            gemini_model=os.getenv("VF_GEMINI_MODEL", "").strip(),
            gemini_source=os.getenv("VF_GEMINI_SOURCE", "").strip(),
            seedance_base_url=os.getenv("VF_SEEDANCE_BASE_URL", "").strip(),
            seedance_api_key=os.getenv("VF_SEEDANCE_API_KEY", "").strip(),
            seedance_model=os.getenv("VF_SEEDANCE_MODEL", "").strip(),
            seedance_auth_file=_seedance_auth_file(),
            allow_local_copy_fallback=_env_bool("VF_ALLOW_LOCAL_COPY_FALLBACK", False),
            e2e_fake_creative_context=_env_bool("VF_E2E_FAKE_CREATIVE_CONTEXT", False),
        )

    def ensure_directories(self) -> None:
        for path in (
            self.data_dir,
            self.workspace,
            self.skills_dir,
            self.disabled_skills_dir,
            self.uploads_dir,
            self.artifacts_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings.from_env()
