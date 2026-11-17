from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from video_fission.config import PROJECT_ROOT, Settings


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    settings = Settings(
        project_root=PROJECT_ROOT,
        data_dir=data_dir,
        workspace=data_dir / "workspace",
        skills_dir=data_dir / "skills",
        bundled_skills_dir=PROJECT_ROOT / "skills",
        disabled_skills_dir=data_dir / "skills_disabled",
        uploads_dir=data_dir / "uploads",
        artifacts_dir=data_dir / "artifacts",
        frontend_dist=PROJECT_ROOT / "frontend" / "dist",
        database_path=data_dir / "test.db",
        codex_bin=shutil.which("codex"),
        codex_timeout_seconds=180,
        approval_timeout_seconds=30,
        host="127.0.0.1",
        port=8000,
    )
    settings.ensure_directories()
    return settings
