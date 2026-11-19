from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

os.environ["VF_LOG_FILE"] = "0"

from video_fission import providers  # noqa: E402
from video_fission.config import PROJECT_ROOT, Settings  # noqa: E402

FIXTURE_VIDEO = PROJECT_ROOT / "backend" / "tests" / "fixtures" / "sample-reference.mp4"


@pytest.fixture(autouse=True)
def stub_remote_video(monkeypatch, request):
    node_path = Path(str(getattr(request.node, "path", request.node.fspath)))
    if node_path.name == "test_seedance_provider.py":
        yield
        return

    def fake_generate_remote_video(*args, **kwargs):
        dest = Path(args[10])
        shutil.copyfile(FIXTURE_VIDEO, dest)
        return dest

    monkeypatch.setattr(providers, "generate_remote_video", fake_generate_remote_video)
    yield


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
        auth_required=False,
    )
    settings.ensure_directories()
    return settings
