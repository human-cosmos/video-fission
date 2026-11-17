from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _env_path(name: str, default: Path) -> Path:
    raw = os.getenv(name, "").strip()
    return Path(raw).expanduser().resolve() if raw else default.resolve()


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
