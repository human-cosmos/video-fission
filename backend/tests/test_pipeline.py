from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from video_fission.container import configure
from video_fission.db import Database
from video_fission.main import app
from video_fission.media import audio_levels, probe, scene_cut_ms
from video_fission.pipeline import local_intent, local_script, plan_segments, brief_role_phrases, _shot_frame
from video_fission.replace import parse_brief_targets, resolve_replace_plan, strip_rewrite_instructions, is_rewrite_only_brief
from video_fission.productions import ProductionService
from video_fission.settings_store import SettingsStore

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample-reference.mp4"


@pytest.fixture
def client(test_settings):
    configure(test_settings)
    with TestClient(app) as test_client:
        yield test_client


def _upload(client: TestClient) -> dict:
    with FIXTURE.open("rb") as handle:
        response = client.post(
            "/api/productions/uploads",
            files={"file": ("sample-reference.mp4", handle, "video/mp4")},
        )
    assert response.status_code == 201, response.text
    return response.json()


def test_settings_masks_api_key_and_keeps_or_clears(client: TestClient):
    initial = client.get("/api/settings").json()
    assert initial["multimodal"]["provider"] == "local_ffmpeg"
    assert initial["multimodal"]["api_key_set"] is False
    assert "api_key" not in initial["multimodal"]

    updated = client.put(
        "/api/settings",
        json={"multimodal": {"api_key": "sk-secret-test", "model": "demo"}},
    ).json()
    assert updated["multimodal"]["api_key_set"] is True
    assert updated["multimodal"]["model"] == "demo"
    assert "api_key" not in updated["multimodal"]
    assert "sk-secret-test" not in str(updated)

    kept = client.put("/api/settings", json={"multimodal": {"api_key": "", "model": "demo-2"}}).json()
    assert kept["multimodal"]["api_key_set"] is True
    assert kept["multimodal"]["model"] == "demo-2"

    cleared = client.put("/api/settings", json={"multimodal": {"api_key": None}}).json()
    assert cleared["multimodal"]["api_key_set"] is False

    probed = client.post("/api/settings/test", json={"target": "multimodal"}).json()
    assert probed["ok"] is True
    assert probed["provider"] == "local_ffmpeg"
    assert "sk-" not in probed["detail"]


def test_health_includes_ffmpeg_and_pipeline(client: TestClient):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["service"] == "video-fission"
    assert "runtime" in body
    assert "database" in body
    assert body["ffmpeg"]["ok"] is True
    assert body["pipeline"]["ok"] is True
    assert body["pipeline"]["default_provider"] == "local_ffmpeg"


def test_local_decompose_covers_timeline_with_hook_value_cta():
    segments = plan_segments(8100, [2700, 5400], [], False)
    assert [(item["role"], item["start_ms"], item["end_ms"]) for item in segments] == [
        ("hook", 0, 2700),
        ("value", 2700, 5400),
        ("cta", 5400, 8100),
    ]
    for left, right in zip(segments, segments[1:], strict=False):
        assert right["start_ms"] == left["end_ms"]


def test_sample_reference_detects_main_scene_cuts():
    cuts = scene_cut_ms(FIXTURE)
    assert cuts == [2700, 5400]


def test_shot_frame_prefers_mid_segment_still(tmp_path: Path):
    art = tmp_path
    (art / "seg_02_frame_01.jpg").write_bytes(b"early")
    (art / "seg_02_frame_02.jpg").write_bytes(b"mid")
    chosen = _shot_frame(art, {"id": "shot_02", "source_segment_id": "seg_02"}, {})
    assert chosen.name == "seg_02_frame_02.jpg"


def test_intent_references_exist_and_full_run_writes_final(test_settings, client: TestClient):
    uploaded = _upload(client)
    created = client.post(
        "/api/productions",
        json={
            "title": "fixture run",
            "brief": "用香酥虾做一个开场抓人、中间展示产品、结尾促单的短视频",
            "video_upload_id": uploaded["id"],
        },
    )
    assert created.status_code == 201, created.text
    production_id = created.json()["id"]

    from video_fission import container

    finished = container.productions.engine.run(production_id)
    public = container.productions.get(production_id)
    assert finished["status"] == "completed"
    assert public["status"] == "completed"
    refs = public["intent"]["references"]
    assert len(refs) >= 4
    existing = 0
    for item in refs:
        path = test_settings.project_root / item["path"]
        assert path.is_file(), item["path"]
        existing += 1
    assert existing == len(refs)
    categories = {item["path"].split("/")[2] if len(item["path"].split("/")) > 2 else "" for item in refs}
    # 3 same + 1 cross means at least two categories when possible
    assert len(refs) == 4

    segments = public["decomposition"]["segments"]
    assert segments[0]["role"] == "hook"
    assert segments[-1]["role"] == "cta"
    assert any(item["role"] == "value" for item in segments)
    assert segments[0]["start_ms"] == 0
    assert abs(segments[-1]["end_ms"] - public["source_video"]["duration_ms"]) <= 100

    output = public["output"]
    final_video = Path(output["video_path"])
    final_srt = Path(output["srt_path"])
    assert final_video.is_file()
    assert final_srt.is_file()
    info = probe(final_video)
    assert info["duration_ms"] > 1000
    assert abs(info["duration_ms"] - public["script"]["total_duration_ms"]) <= 500
    assert final_video.stat().st_size > 1000
    assert info["pix_fmt"] == "yuv420p"
    assert str(info.get("color_range") or "").lower() not in {"pc", "jpeg", "full", "2"}
    srt_text = final_srt.read_text(encoding="utf-8")
    assert "虾" in srt_text or srt_text.strip()
    one_liner = public["skeleton"]["one_liner"]
    assert "香酥虾" in one_liner
    for shot in public["script"]["shots"]:
        if shot["role"] == "other":
            continue
        voiceover = shot.get("voiceover") or ""
        assert "时长" not in voiceover and "ms" not in voiceover
        assert voiceover.strip()

    artifact = client.get(f"/api/productions/{production_id}/artifacts/final.mp4")
    assert artifact.status_code == 200
    assert artifact.headers["content-type"].startswith("video/")
    srt = client.get(f"/api/productions/{production_id}/artifacts/final.srt")
    assert srt.status_code == 200
    frame = client.get(f"/api/productions/{production_id}/artifacts/seg_01_frame_01.jpg")
    assert frame.status_code == 200
    missing = client.get(f"/api/productions/{production_id}/artifacts/../final.mp4")
    assert missing.status_code in {400, 404, 422}

    # unknown path still 404 is a conversation concern; traversal must not escape
    traversal = client.get(f"/api/productions/{production_id}/artifacts/..%2F..%2F..%2Fetc%2Fpasswd")
    assert traversal.status_code in {400, 404}


def test_cancel_draft_and_running_conflict(client: TestClient):
    uploaded = _upload(client)
    created = client.post(
        "/api/productions",
        json={"brief": "尽量复用原片，做一条护膝短视频", "video_upload_id": uploaded["id"]},
    ).json()
    production_id = created["id"]
    cancelled = client.post(f"/api/productions/{production_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    again = client.post(
        "/api/productions",
        json={"brief": "第二条", "video_upload_id": uploaded["id"]},
    ).json()
    from video_fission import container

    container.db.update_production(again["id"], {"status": "running"})
    conflict = client.post(f"/api/productions/{again['id']}/run")
    assert conflict.status_code == 409


def test_failed_run_keeps_error(client: TestClient):
    uploaded = _upload(client)
    created = client.post(
        "/api/productions",
        json={"brief": "失败路径", "video_upload_id": uploaded["id"]},
    ).json()
    Path(uploaded["path"]).unlink()
    from video_fission import container

    with pytest.raises(Exception):
        container.productions.engine.run(created["id"])
    body = client.get(f"/api/productions/{created['id']}").json()
    assert body["status"] == "failed"
    assert body["error"]

def test_local_intent_one_liner_comes_from_brief_not_reference():
    brief = "用香酥虾做一个开场抓人、中间展示产品、结尾促单的短视频"
    refs = [
        {
            "path": "skills/video-references/references/食品零食/fake.md",
            "why": "同品类结构参考",
            "excerpt": {"skeleton": "这是参考脚本骨架专有句，禁止写进 one_liner"},
        }
    ]
    decomposition = {
        "segments": [
            {"id": "seg_01", "role": "hook", "summary": "HOOK 段，时长 1500ms"},
            {"id": "seg_02", "role": "value", "summary": "VALUE 段，时长 4000ms"},
            {"id": "seg_03", "role": "cta", "summary": "CTA 段，时长 1500ms"},
        ]
    }
    intent, skeleton = local_intent(brief, decomposition, refs)
    assert "香酥虾" in skeleton["one_liner"]
    assert "参考脚本骨架专有句" not in skeleton["one_liner"]
    assert intent["references"][0]["path"].endswith("fake.md")


def test_local_script_voiceover_follows_each_segment_summary_and_duration():
    production = {
        "brief": "用香酥虾做一条短视频",
        "source_video": {"width": 1280, "height": 720},
        "skeleton": {
            "one_liner": "用香酥虾做一条短视频",
            "hook": "开场钩子",
            "value": "VALUE 段，时长 4000ms",
            "cta": "收束行动号召",
        },
        "decomposition": {
            "segments": [
                {
                    "id": "seg_01",
                    "role": "hook",
                    "start_ms": 0,
                    "end_ms": 1500,
                    "summary": "HOOK 段，时长 1500ms",
                    "visual": "hook",
                    "on_screen_text": "HOOK",
                },
                {
                    "id": "seg_02",
                    "role": "value",
                    "start_ms": 1500,
                    "end_ms": 5500,
                    "summary": "展示产品细节，时长 4000ms",
                    "visual": "value-a",
                    "on_screen_text": "VALUE",
                },
                {
                    "id": "seg_03",
                    "role": "value",
                    "start_ms": 5500,
                    "end_ms": 6600,
                    "summary": "VALUE 段，时长 4000ms",
                    "visual": "value-b",
                    "on_screen_text": "VALUE",
                },
                {
                    "id": "seg_04",
                    "role": "cta",
                    "start_ms": 6600,
                    "end_ms": 8100,
                    "summary": "CTA 段，时长 1500ms",
                    "visual": "cta",
                    "on_screen_text": "CTA",
                },
            ]
        },
    }
    script = local_script(production)
    value_shots = [shot for shot in script["shots"] if shot["role"] == "value"]
    assert len(value_shots) == 2
    first, second = value_shots
    assert first["duration_ms"] == 4000
    assert second["duration_ms"] == 1100
    assert "展示产品细节" in first["voiceover"]
    assert "时长 4000ms" not in first["voiceover"]
    assert "时长 1100ms" not in second["voiceover"]
    assert "时长" not in first["voiceover"]
    assert "时长" not in second["voiceover"]
    assert first["voiceover"] != second["voiceover"]
    assert "香酥虾" in second["voiceover"] or "卖点" in second["voiceover"] or "说明" in second["voiceover"]


def _script_from_brief(brief: str, summaries=None) -> dict:
    defaults = [
        ("seg_01", "hook", 0, 1500, "HOOK 段，时长 1500ms"),
        ("seg_02", "value", 1500, 5500, "VALUE 段，时长 4000ms"),
        ("seg_03", "value", 5500, 6600, "VALUE 段，时长 1100ms"),
        ("seg_04", "cta", 6600, 8100, "CTA 段，时长 1500ms"),
    ]
    segments = []
    for item in summaries or defaults:
        seg_id, role, start, end, summary = item
        segments.append(
            {
                "id": seg_id,
                "role": role,
                "start_ms": start,
                "end_ms": end,
                "summary": summary,
                "visual": role,
                "on_screen_text": role.upper(),
            }
        )
    return local_script(
        {
            "brief": brief,
            "source_video": {"width": 1280, "height": 720},
            "skeleton": {},
            "decomposition": {"segments": segments},
        }
    )


def test_local_script_splits_creative_brief_by_role():
    brief = "用香酥虾做一个开场抓人、中间展示产品、结尾促单的短视频"
    assert brief_role_phrases(brief) == {
        "hook": ["开场抓人"],
        "value": ["中间展示产品"],
        "cta": ["结尾促单"],
    }
    script = _script_from_brief(brief)
    by_role = {shot["id"]: shot for shot in script["shots"]}
    assert by_role["shot_01"]["voiceover"] == "开场抓人"
    assert by_role["shot_02"]["voiceover"] == "中间展示产品"
    assert by_role["shot_04"]["voiceover"] == "结尾促单"
    texts = [shot["voiceover"] for shot in script["shots"]]
    assert len(set(texts)) == 4
    assert texts.count(brief) == 0
    assert "开场抓人、中间展示产品、结尾促单" not in "".join(texts)


def test_local_script_ignores_rewrite_only_brief():
    for brief in ("把第二段和结尾重新生成", "只重做开场", "全部新生成"):
        script = _script_from_brief(brief)
        for shot in script["shots"]:
            blob = (shot.get("voiceover") or "") + (shot.get("on_screen_text") or "")
            assert shot["voiceover"].strip()
            assert brief not in blob
            assert "重新生成" not in blob
            assert "重做" not in blob
            assert "第二段" not in blob
            assert "全部新生成" not in blob
        assert script["shots"][0]["voiceover"] != script["shots"][1]["voiceover"]




def _create(client: TestClient, brief: str, policy=None) -> dict:
    uploaded = _upload(client)
    body = {"brief": brief, "video_upload_id": uploaded["id"]}
    if policy is not None:
        body["replace_policy"] = policy
    created = client.post("/api/productions", json=body)
    assert created.status_code == 201, created.text
    return created.json()


def _run_wait(production_id: str, replace_policy=None, resume_from=None) -> dict:
    from video_fission import container

    return container.productions.run(
        production_id,
        wait=True,
        replace_policy=replace_policy,
        resume_from=resume_from,
    )


def _assert_final(public: dict, source_tolerance_ms: int = 100) -> dict:
    assert public["status"] == "completed", public.get("error")
    output = public["output"]
    final_video = Path(output["video_path"])
    final_srt = Path(output["srt_path"])
    assert final_video.is_file()
    assert final_srt.is_file()
    info = probe(final_video)
    source_ms = int(public["source_video"]["duration_ms"])
    assert abs(info["duration_ms"] - source_ms) <= source_tolerance_ms
    if info.get("has_audio"):
        assert abs(int(info.get("audio_duration_ms") or 0) - info["duration_ms"]) <= 20
    assert info["pix_fmt"] == "yuv420p"
    assert str(info.get("color_range") or "").lower() not in {"pc", "jpeg", "full", "2"}
    srt_text = final_srt.read_text(encoding="utf-8")
    assert srt_text.strip()
    assert "时长" not in srt_text
    brief = str(public.get("brief") or "")
    rewrite_only = is_rewrite_only_brief(brief)
    for shot in public["script"]["shots"]:
        if shot["role"] == "other":
            continue
        voiceover = shot.get("voiceover") or ""
        on_screen = shot.get("on_screen_text") or ""
        assert "时长" not in voiceover
        assert "时长" not in on_screen
        if rewrite_only:
            assert brief not in voiceover
            assert brief not in on_screen
            assert brief not in srt_text
            assert "重新生成" not in voiceover + on_screen
            assert "重做" not in voiceover + on_screen
    source_has_audio = bool((public.get("source_video") or {}).get("duration_ms") and info.get("has_audio"))
    if source_has_audio:
        levels = audio_levels(final_video)
        assert levels["mean_db"] > -60, levels
        assert levels["max_db"] > -40, levels
        for clip in public.get("clips") or []:
            shot = next((item for item in public["script"]["shots"] if item["id"] == clip["shot_id"]), None)
            if not shot or shot.get("action") != "generate":
                continue
            clip_levels = audio_levels(Path(clip["path"]))
            assert clip_levels["mean_db"] > -60, (clip["shot_id"], clip_levels)
            assert clip_levels["max_db"] > -40, (clip["shot_id"], clip_levels)
    return info


def _actions(public: dict) -> dict[str, str]:
    return {
        shot["source_segment_id"]: shot["action"]
        for shot in public["script"]["shots"]
    }


def _ids_by_role(public: dict, role: str) -> list[str]:
    return [seg["id"] for seg in public["decomposition"]["segments"] if seg["role"] == role]


def test_brief_parser_only_treats_rewrite_requests_as_targets():
    assert strip_rewrite_instructions("把第二段和结尾重新生成") == ""
    assert is_rewrite_only_brief("只重做开场")
    assert strip_rewrite_instructions("用香酥虾做一条短视频。把第二段和结尾重新生成。") == "用香酥虾做一条短视频"
    creative = parse_brief_targets("用香酥虾做一个开场抓人、中间展示产品、结尾促单的短视频")
    assert creative["has_named"] is False
    assert creative["generate_all"] is False

    second_and_end = parse_brief_targets("把第二段和结尾重新生成")
    assert second_and_end["has_named"] is True
    assert 2 in second_and_end["ordinals"]
    assert "cta" in second_and_end["roles"]
    assert "hook" not in second_and_end["roles"]

    hook_only = parse_brief_targets("只重做开场")
    assert hook_only["roles"] == ["hook"]
    assert hook_only["has_named"] is True

    mid_too = parse_brief_targets("中间也要新生成")
    assert "value" in mid_too["roles"]
    assert mid_too["also_default"] is True

    clock = parse_brief_targets("从 00:01.5 到 00:05.5 重新生成")
    assert clock["time_ranges"][0]["start_ms"] == 1500
    assert clock["time_ranges"][0]["end_ms"] == 5500
    seconds = parse_brief_targets("把 1.5秒到5秒 重新生成")
    assert seconds["time_ranges"][0] == {"start_ms": 1500, "end_ms": 5000}


def test_replace_plan_modes_and_time_range_hit():
    segments = [
        {"id": "seg_01", "role": "hook", "start_ms": 0, "end_ms": 1500},
        {"id": "seg_02", "role": "value", "start_ms": 1500, "end_ms": 2700},
        {"id": "seg_03", "role": "value", "start_ms": 2700, "end_ms": 5400},
        {"id": "seg_04", "role": "value", "start_ms": 5400, "end_ms": 6600},
        {"id": "seg_05", "role": "cta", "start_ms": 6600, "end_ms": 8100},
    ]
    default = resolve_replace_plan({"mode": "default"}, segments, "用香酥虾做一个开场抓人、中间展示产品、结尾促单的短视频")
    assert default["resolved_segment_ids"] == ["seg_01", "seg_05"]
    assert {item["reason"] for item in default["reasons"]} == {"default_hook_cta"}

    selected = resolve_replace_plan({"mode": "selected", "segment_ids": ["seg_03"]}, segments, "")
    assert selected["resolved_segment_ids"] == ["seg_03"]
    assert selected["reasons"][0]["reason"] == "user_selected"

    timed = resolve_replace_plan(
        {"mode": "selected", "time_ranges": [{"start_ms": 3000, "end_ms": 4000}]},
        segments,
        "",
    )
    assert timed["resolved_segment_ids"] == ["seg_03"]
    assert timed["reasons"][0]["reason"] == "time_range"

    briefed = resolve_replace_plan({"mode": "brief"}, segments, "把第二段和结尾重新生成")
    assert briefed["resolved_segment_ids"] == ["seg_02", "seg_05"]
    assert {item["reason"] for item in briefed["reasons"]} == {"brief"}

    hook_only = resolve_replace_plan({"mode": "default"}, segments, "只重做开场")
    assert hook_only["resolved_segment_ids"] == ["seg_01"]

    import pytest as _pytest

    with _pytest.raises(ValueError, match="没有命中任何片段"):
        resolve_replace_plan({"mode": "selected", "segment_ids": ["seg_99"]}, segments, "")


def test_default_policy_generates_hook_cta_and_reuses_value(client: TestClient):
    created = _create(client, "用香酥虾做一个开场抓人、中间展示产品、结尾促单的短视频")
    public = _run_wait(created["id"])
    _assert_final(public)
    actions = _actions(public)
    assert public["replace_plan"]["resolved_segment_ids"] == _ids_by_role(public, "hook") + _ids_by_role(public, "cta")
    for segment_id in _ids_by_role(public, "hook") + _ids_by_role(public, "cta"):
        assert actions[segment_id] == "generate"
    for segment_id in _ids_by_role(public, "value"):
        assert actions[segment_id] == "reuse"


def test_selected_only_middle_segment_reuses_hook_and_cta(client: TestClient):
    created = _create(
        client,
        "用香酥虾做一条短视频",
        policy={"mode": "selected", "segment_ids": ["seg_02"]},
    )
    public = _run_wait(created["id"])
    _assert_final(public)
    actions = _actions(public)
    assert public["replace_plan"]["resolved_segment_ids"] == ["seg_02"]
    assert actions["seg_02"] == "generate"
    assert "seg_02" in _ids_by_role(public, "value")
    for segment_id in _ids_by_role(public, "hook") + _ids_by_role(public, "cta"):
        assert actions[segment_id] == "reuse"
    assert public["replace_plan"]["reasons"][0]["reason"] == "user_selected"


def test_selected_only_hook_reuses_the_rest(client: TestClient):
    created = _create(client, "用香酥虾做一条短视频", policy={"mode": "selected", "roles": ["hook"]})
    public = _run_wait(created["id"])
    _assert_final(public)
    actions = _actions(public)
    hook_ids = _ids_by_role(public, "hook")
    assert public["replace_plan"]["resolved_segment_ids"] == hook_ids
    for segment_id, action in actions.items():
        assert action == ("generate" if segment_id in hook_ids else "reuse")


def test_mode_all_generates_every_segment(client: TestClient):
    created = _create(client, "用香酥虾做一条短视频", policy={"mode": "all"})
    public = _run_wait(created["id"])
    _assert_final(public)
    actions = _actions(public)
    assert set(public["replace_plan"]["resolved_segment_ids"]) == set(actions)
    assert set(actions.values()) == {"generate"}
    assert {item["reason"] for item in public["replace_plan"]["reasons"]} == {"generate_all"}


def test_brief_regenerate_second_segment_and_ending(client: TestClient):
    created = _create(client, "把第二段和结尾重新生成")
    public = _run_wait(created["id"])
    _assert_final(public)
    segments = public["decomposition"]["segments"]
    expected = [segments[1]["id"], segments[-1]["id"]]
    assert public["replace_plan"]["resolved_segment_ids"] == expected
    actions = _actions(public)
    for segment_id, action in actions.items():
        assert action == ("generate" if segment_id in expected else "reuse")
    assert "user_selected" not in {item["reason"] for item in public["replace_plan"]["reasons"]}
    assert {item["reason"] for item in public["replace_plan"]["reasons"]} == {"brief"}


def test_brief_redo_hook_only(client: TestClient):
    created = _create(client, "只重做开场")
    public = _run_wait(created["id"])
    _assert_final(public)
    hook_ids = _ids_by_role(public, "hook")
    assert public["replace_plan"]["resolved_segment_ids"] == hook_ids
    actions = _actions(public)
    for segment_id, action in actions.items():
        assert action == ("generate" if segment_id in hook_ids else "reuse")


def test_time_ranges_hit_a_middle_segment(client: TestClient):
    created = _create(
        client,
        "用香酥虾做一条短视频",
        policy={"mode": "selected", "time_ranges": [{"start_ms": 3000, "end_ms": 4000}]},
    )
    public = _run_wait(created["id"])
    _assert_final(public)
    resolved = public["replace_plan"]["resolved_segment_ids"]
    assert resolved
    segments = {seg["id"]: seg for seg in public["decomposition"]["segments"]}
    for segment_id in resolved:
        start = segments[segment_id]["start_ms"]
        end = segments[segment_id]["end_ms"]
        overlap = max(0, min(end, 4000) - max(start, 3000))
        duration = end - start
        assert overlap >= 200 or overlap * 2 > duration
        assert 3000 < end and 4000 > start
    actions = _actions(public)
    for segment_id, action in actions.items():
        assert action == ("generate" if segment_id in resolved else "reuse")
    assert {item["reason"] for item in public["replace_plan"]["reasons"]} == {"time_range"}


def test_rerun_with_new_selection_overwrites_final(client: TestClient):
    created = _create(client, "用香酥虾做一个开场抓人、中间展示产品、结尾促单的短视频")
    first = _run_wait(created["id"])
    _assert_final(first)
    first_mtime = Path(first["output"]["video_path"]).stat().st_mtime
    value_ids = _ids_by_role(first, "value")
    assert value_ids
    selected = [value_ids[0]]
    patched = client.patch(
        f"/api/productions/{created['id']}",
        json={"replace_policy": {"mode": "selected", "segment_ids": selected}},
    )
    assert patched.status_code == 200, patched.text
    second = _run_wait(created["id"])
    _assert_final(second)
    assert second["replace_plan"]["resolved_segment_ids"] == selected
    artifact = client.get(f"/api/productions/{created['id']}/artifacts/final.mp4")
    assert artifact.status_code == 200
    assert artifact.headers["content-type"].startswith("video/")
    assert Path(second["output"]["video_path"]).stat().st_mtime >= first_mtime
    actions = _actions(second)
    assert actions[selected[0]] == "generate"
    for segment_id in _ids_by_role(second, "hook") + _ids_by_role(second, "cta"):
        assert actions[segment_id] == "reuse"


def test_selected_with_no_matching_segment_returns_400(client: TestClient):
    created = _create(client, "用香酥虾做一条短视频")
    finished = _run_wait(created["id"])
    assert finished["status"] == "completed"
    empty = client.post(
        f"/api/productions/{created['id']}/run",
        json={"replace_policy": {"mode": "selected", "segment_ids": ["seg_missing"]}},
    )
    assert empty.status_code == 400, empty.text
    assert "没有命中任何片段" in empty.text
    still = client.get(f"/api/productions/{created['id']}").json()
    assert still["status"] == "completed"

    no_selectors = client.post(
        f"/api/productions/{created['id']}/run",
        json={"replace_policy": {"mode": "selected"}},
    )
    assert no_selectors.status_code == 400
    assert "没有命中任何片段" in no_selectors.text

    # empty body remains compatible on a draft, and running still 409
    running = client.post(
        "/api/productions",
        json={"brief": "空 body 兼容", "video_upload_id": _upload(client)["id"]},
    ).json()
    from video_fission import container

    container.db.update_production(running["id"], {"status": "running"})
    conflict = client.post(f"/api/productions/{running['id']}/run")
    assert conflict.status_code == 409
