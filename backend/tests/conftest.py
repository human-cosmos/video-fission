from __future__ import annotations

from pathlib import Path

import pytest

from video_fission.config import PROJECT_ROOT, Settings

FIXTURE_VIDEO = Path(__file__).resolve().parent / "fixtures" / "sample-reference.mp4"


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    data = tmp_path / "data"
    settings = Settings(
        project_root=PROJECT_ROOT,
        data_dir=data,
        workspace=data / "workspace",
        skills_dir=data / "skills",
        bundled_skills_dir=PROJECT_ROOT / "skills",
        disabled_skills_dir=data / "skills_disabled",
        uploads_dir=data / "uploads",
        artifacts_dir=data / "artifacts",
        frontend_dist=PROJECT_ROOT / "frontend" / "dist",
        database_path=data / "video_fission.db",
        codex_bin="",
        codex_timeout_seconds=30,
        approval_timeout_seconds=5,
        host="127.0.0.1",
        port=18000,
        auth_required=False,
        allow_local_copy_fallback=False,
    )
    settings.ensure_directories()
    return settings


@pytest.fixture
def app_env(test_settings: Settings):
    from video_fission.container import configure
    from video_fission import container

    configure(test_settings)
    return container
