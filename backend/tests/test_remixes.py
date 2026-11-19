from __future__ import annotations

import shutil
import subprocess
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from video_fission.config import Settings
from video_fission.db import Database
from video_fission.pipeline import PipelineEngine
from video_fission.remixes import REMIX_TASK_KIND, ReferenceRemixService
from video_fission.settings_store import SettingsStore


def _production() -> dict:
    segments = [
        {
            "id": "seg_01",
            "role": "hook",
            "start_ms": 0,
            "end_ms": 3000,
            "summary": "开场",
        },
        {
            "id": "seg_02",
            "role": "value",
            "start_ms": 3000,
            "end_ms": 5000,
            "summary": "卖点一",
        },
        {
            "id": "seg_03",
            "role": "value",
            "start_ms": 5000,
            "end_ms": 7000,
            "summary": "卖点二",
        },
        {
            "id": "seg_04",
            "role": "value",
            "start_ms": 7000,
            "end_ms": 9000,
            "summary": "卖点三",
        },
        {
            "id": "seg_05",
            "role": "cta",
            "start_ms": 9000,
            "end_ms": 11000,
            "summary": "结尾",
        },
    ]
    return {
        "id": "rmx_test",
        "source_video": {"duration_ms": 11000, "width": 1080, "height": 1920, "fps": 30},
        "decomposition": {
            "provider": "gemini_gateway",
            "opening": {"start_ms": 0, "end_ms": 3000, "segment_ids": ["seg_01"]},
            "segments": segments,
        },
        "remix_plan": {"strategy": "seeded_shuffle", "seed": "stable-seed"},
    }


def test_plan_preserves_bookends_and_uses_each_middle_segment_once() -> None:
    service = object.__new__(ReferenceRemixService)
    plan = service._build_plan(_production())

    assert plan["opening"] == {"start_ms": 0, "end_ms": 3000}
    assert plan["ending"] == {"start_ms": 9000, "end_ms": 11000}
    assert plan["ordered_units"][0]["kind"] == "opening"
    assert plan["ordered_units"][-1]["kind"] == "ending"
    assert set(plan["middle_order"]) == {"seg_02", "seg_03", "seg_04"}
    assert len(plan["middle_order"]) == len(set(plan["middle_order"]))
    assert plan["total_duration_ms"] == 11000
    assert plan["video_model_called"] is False


def test_seeded_shuffle_changes_value_order_without_duplicates() -> None:
    service = object.__new__(ReferenceRemixService)
    plan = service._build_plan(_production())

    assert plan["middle_order"] != ["seg_02", "seg_03", "seg_04"]
    assert set(plan["middle_order"]) == {"seg_02", "seg_03", "seg_04"}


def test_non_value_middle_anchor_keeps_its_slot() -> None:
    middle = [
        {"id": "value_a", "role": "value"},
        {"id": "anchor", "role": "other"},
        {"id": "value_b", "role": "value"},
        {"id": "value_c", "role": "value"},
    ]
    reorderable = [item for item in middle if item["role"] == "value"]

    ordered = ReferenceRemixService._ordered_middle_ids(
        middle,
        reorderable,
        strategy="reverse",
        seed="unused",
        middle_order=None,
    )

    assert ordered[1] == "anchor"
    assert ordered[0] == "value_c"
    assert ordered[2:] == ["value_b", "value_a"]


def test_manual_order_must_contain_every_middle_segment_once() -> None:
    service = object.__new__(ReferenceRemixService)
    plan = service._build_plan(
        _production(),
        strategy="manual",
        middle_order=["seg_04", "seg_02", "seg_03"],
    )
    assert plan["middle_order"] == ["seg_04", "seg_02", "seg_03"]

    with pytest.raises(ValueError, match="完整包含所有片段"):
        service._build_plan(
            _production(),
            strategy="manual",
            middle_order=["seg_04", "seg_02"],
        )


def test_manual_order_cannot_move_non_value_anchor() -> None:
    service = object.__new__(ReferenceRemixService)
    production = _production()
    production["decomposition"]["segments"][2]["role"] = "other"

    with pytest.raises(ValueError, match="不能移动非 value"):
        service._build_plan(
            production,
            strategy="manual",
            middle_order=["seg_02", "seg_04", "seg_03"],
        )


def test_local_opening_fallback_is_clamped_to_leave_middle_space() -> None:
    service = object.__new__(ReferenceRemixService)
    production = _production()
    production["decomposition"]["opening"] = {
        "start_ms": 0,
        "end_ms": 11000,
        "method": "local_fallback",
        "segment_ids": [],
    }

    plan = service._build_plan(production)

    assert plan["opening"]["end_ms"] == 3000
    assert set(plan["middle_order"]) == {"seg_02", "seg_03", "seg_04"}
    assert plan["warnings"]


def test_database_keeps_creative_and_remix_lists_isolated(tmp_path: Path) -> None:
    db = Database(tmp_path / "remix.db")
    db.initialize()
    upload = db.create_video_upload(
        {
            "id": "upl_test",
            "name": "sample.mp4",
            "path": str(tmp_path / "sample.mp4"),
            "size": 10,
            "content_type": "video/mp4",
            "duration_ms": 11000,
            "width": 1080,
            "height": 1920,
            "fps": 30,
        }
    )
    base = {
        "user_id": "user_test",
        "title": "test",
        "brief": "",
        "source_video": {},
        "video_upload_id": upload["id"],
    }
    db.create_production(
        {
            **base,
            "id": "prd_creative",
        }
    )
    db.create_production(
        {
            **base,
            "id": "rmx_reference",
            "task_kind": REMIX_TASK_KIND,
            "remix_plan": {"phase": "awaiting_order"},
        }
    )

    creative = db.list_productions(user_id="user_test")
    remixes = db.list_productions(user_id="user_test", task_kind=REMIX_TASK_KIND)
    assert [item["id"] for item in creative] == ["prd_creative"]
    assert [item["id"] for item in remixes] == ["rmx_reference"]
    assert db.get_production(
        "rmx_reference",
        "user_test",
        task_kind="creative",
    ) is None


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")
def test_render_completes_single_reference_only_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            shutil.which("ffmpeg"),
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x568:rate=30:duration=8",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100:duration=8",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    monkeypatch.setenv("VF_DATA_DIR", str(tmp_path / "settings-data"))
    monkeypatch.setenv("VF_WORKSPACE", str(tmp_path / "settings-workspace"))
    settings = replace(
        Settings.from_env(),
        data_dir=tmp_path / "data",
        workspace=tmp_path / "workspace",
        skills_dir=tmp_path / "skills",
        bundled_skills_dir=Path("skills").resolve(),
        disabled_skills_dir=tmp_path / "disabled",
        uploads_dir=tmp_path / "uploads",
        artifacts_dir=tmp_path / "artifacts",
        database_path=tmp_path / "remix-render.db",
    )
    settings.ensure_directories()
    db = Database(settings.database_path)
    db.initialize()
    store = SettingsStore(db, settings)
    engine = PipelineEngine(settings, db, store)
    service = ReferenceRemixService(settings, db, store, engine)
    upload = db.create_video_upload(
        {
            "id": "upl_render",
            "name": "source.mp4",
            "path": str(source),
            "size": source.stat().st_size,
            "content_type": "video/mp4",
            "duration_ms": 8000,
            "width": 320,
            "height": 568,
            "fps": 30,
        }
    )
    decomposition = {
        "provider": "local_ffmpeg",
        "opening": {"start_ms": 0, "end_ms": 2000, "segment_ids": ["seg_01"]},
        "segments": [
            {"id": "seg_01", "role": "hook", "start_ms": 0, "end_ms": 2000, "summary": "开场"},
            {"id": "seg_02", "role": "value", "start_ms": 2000, "end_ms": 4000, "summary": "卖点一"},
            {"id": "seg_03", "role": "value", "start_ms": 4000, "end_ms": 6000, "summary": "卖点二"},
            {"id": "seg_04", "role": "cta", "start_ms": 6000, "end_ms": 8000, "summary": "结尾"},
        ],
    }
    production = {
        "id": "rmx_render",
        "source_video": {
            "upload_id": upload["id"],
            "path": str(source),
            "duration_ms": 8000,
            "width": 320,
            "height": 568,
            "fps": 30,
            "has_audio": True,
        },
        "decomposition": decomposition,
        "remix_plan": {"strategy": "seeded_shuffle", "seed": "render-seed"},
    }
    db.create_production(
        {
            "id": production["id"],
            "task_kind": REMIX_TASK_KIND,
            "title": "render test",
            "brief": "",
            "status": "running",
            "current_stage": "render",
            "source_video": production["source_video"],
            "decomposition": decomposition,
            "video_upload_id": upload["id"],
            "remix_plan": service._build_plan(production),
        }
    )

    service._run_render(production["id"], threading.Event())

    completed = db.get_production(production["id"], task_kind=REMIX_TASK_KIND)
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["current_stage"] == "done"
    assert completed["status"] != "awaiting_video_choice"
    output = completed["output"]
    assert output is not None
    assert Path(output["video_path"]).is_file()
    assert Path(output["video_path"]).stat().st_size > 0
    assert completed["remix_plan"]["video_model_called"] is False
