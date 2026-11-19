from __future__ import annotations

import threading
from pathlib import Path

import pytest

from video_fission import media, providers
from video_fission.db import Database
from video_fission.production_agent import FakeCodexRuntime, ProductionAgentError
from video_fission.production_tools import ProductionToolHost


class Crash(BaseException):
    pass


def ready(env):
    p = env.db.create_production({"id": "prd_test", "brief": "test", "video_upload_id": "upl_test"})
    pid = p["id"]
    env.db.update_production(pid, {
        "status": "running", "selected_script_id": "script_1",
        "source_video": {"path": "/unused.mp4", "width": 720, "height": 1280, "fps": 30},
        "script": {"shots": [{"id": "shot_1", "action": "generate", "duration_ms": 1000,
                               "visual_prompt": "a test shot", "voiceover": "字幕"}]},
    })
    return pid


def host(env):
    return ProductionToolHost(env.productions.engine)


def fake_local(env, monkeypatch):
    def compose(pid, cancel, warnings):
        assert env.db.get_production(pid)["status"] == "running"
        dest = env.productions.engine.artifact_dir(pid) / "final.mp4"
        dest.write_bytes(b"composed")
        env.db.update_production(pid, {"output": {"video_path": str(dest)}})
    def burn(video, srt, dest, style):
        dest.write_bytes(b"burned")
    monkeypatch.setattr(env.productions.engine, "_stage_compose", compose)
    monkeypatch.setattr(media, "burn_subtitles", burn)


def set_plan(env, names):
    env.productions.agent.runtime = FakeCodexRuntime({"steps": [{"tool": n} for n in names]})


def test_compose_and_burn_only_agent_completes(app_env, monkeypatch):
    pid = ready(app_env)
    fake_local(app_env, monkeypatch)
    h = host(app_env)
    composed = h.compose_timeline({"production_id": pid}, {})
    assert app_env.db.get_production(pid)["status"] == "running"
    burned = h.burn_subtitles({"production_id": pid}, {})
    assert app_env.db.get_production(pid)["status"] == "running"
    assert burned["video_path"] != composed["video_path"]
    assert Path(composed["video_path"]).read_bytes() == b"composed"
    set_plan(app_env, ["compose_timeline", "burn_subtitles"])
    result = app_env.productions.agent.run(pid)
    assert result["status"] == "completed"
    assert result["output"]["video_path"] == burned["video_path"]
    jobs = app_env.db.list_production_jobs(pid)
    assert len(jobs) == 2 and all(j["status"] == "succeeded" for j in jobs)


@pytest.mark.parametrize("mode", ["not_run", "failure", "missing_file", "empty_file", "old_file"])
def test_no_false_completion(app_env, monkeypatch, mode):
    pid = ready(app_env)
    fake_local(app_env, monkeypatch)
    h = host(app_env)
    composed = h.compose_timeline({"production_id": pid}, {})
    agent = app_env.productions.agent
    steps = [{"tool": n} for n in ["compose_timeline", "burn_subtitles"]]
    if mode == "failure":
        monkeypatch.setattr(media, "burn_subtitles", lambda *a: (_ for _ in ()).throw(RuntimeError("burn failed")))
        set_plan(app_env, ["compose_timeline", "burn_subtitles"])
        with pytest.raises(ProductionAgentError):
            agent.run(pid)
    else:
        if mode in {"missing_file", "empty_file"}:
            burned = h.burn_subtitles({"production_id": pid}, {})
            path = Path(burned["video_path"])
            path.unlink() if mode == "missing_file" else path.write_bytes(b"")
        completed = ["compose_timeline"] if mode == "not_run" else ["compose_timeline", "burn_subtitles"]
        agent._finalize(pid, steps, completed, threading.Event())
    assert app_env.db.get_production(pid)["status"] == "failed"
    assert Path(composed["video_path"]).exists()


def remote_env(env, monkeypatch):
    pid = ready(env)
    engine = env.productions.engine
    monkeypatch.setattr(engine, "_settings_for_production", lambda pid: (
        {"video_generator": {"provider": "seedance", "base_url": "https://fake.invalid", "model": "test"}},
        {"video_generator": "unit-test-placeholder"}))
    monkeypatch.setattr(engine, "_seedance_input_contents", lambda *a: [])
    monkeypatch.setattr(media, "probe", lambda path: {"duration_ms": 1000, "has_audio": True})
    monkeypatch.setattr(media, "normalize_video_clip", lambda src, dest, *a: dest.write_bytes(src.read_bytes()))
    return pid


def test_submit_id_persisted_then_crash_resume_and_idempotency(app_env, monkeypatch):
    pid = remote_env(app_env, monkeypatch)
    calls = []
    def request(url, key, **kwargs):
        calls.append(kwargs.get("payload"))
        if kwargs.get("payload") is not None:
            return {"data": {"task_id": "remote-42"}}
        job = app_env.db.list_production_jobs(pid)[0]
        assert job["external_task_id"] == "remote-42"
        assert job["status"] == "running"
        raise Crash()
    monkeypatch.setattr(providers, "_request_seedance", request)
    engine = app_env.productions.engine
    with pytest.raises(Crash):
        engine._stage_generate(pid, threading.Event(), [])
    app_env.db = Database(app_env.db.path)
    engine.db = app_env.db
    assert pid in app_env.db.recover_production_jobs()
    assert app_env.db.list_production_jobs(pid)[0]["status"] == "running"
    def poll(url, key, **kwargs):
        assert kwargs.get("payload") is None
        assert "remote-42" in url
        calls.append(None)
        return {"data": {"status": "completed", "video_url": "https://fake.invalid/clip.mp4"}}
    monkeypatch.setattr(providers, "_request_seedance", poll)
    monkeypatch.setattr(providers, "_download_seedance_video", lambda url, dest, *a: dest.write_bytes(b"x" * 2000))
    engine._stage_generate(pid, threading.Event(), [])
    engine._stage_generate(pid, threading.Event(), [])
    assert len([c for c in calls if c is not None]) == 1
    assert len(calls) == 3
    job = app_env.db.list_production_jobs(pid)[0]
    assert job["status"] == "succeeded" and job["attempt_count"] == 2
    assert "unit-test-placeholder" not in str(job)


def test_script_reselection_never_reuses_old_job(app_env, monkeypatch):
    pid = remote_env(app_env, monkeypatch)
    submissions = []
    def fake_generate(*args, **kwargs):
        submissions.append(1)
        kwargs["on_submitted"](f"remote-{len(submissions)}")
        args[10].write_bytes(b"x" * 2000)
    monkeypatch.setattr(providers, "generate_remote_video", fake_generate)
    engine = app_env.productions.engine
    engine._stage_generate(pid, threading.Event(), [])
    first = app_env.db.get_production(pid)["clips"][0]["path"]
    app_env.db.update_production(pid, {"selected_script_id": "script_2"})
    engine._stage_generate(pid, threading.Event(), [])
    assert len(submissions) == 2
    assert first != app_env.db.get_production(pid)["clips"][0]["path"]


def test_cancel_jobs_and_stop_poll_without_download(app_env, monkeypatch):
    pid = remote_env(app_env, monkeypatch)
    cancel = threading.Event()
    app_env.productions._cancels[pid] = cancel
    done = app_env.db.create_production_job({"production_id": pid, "tool": "test", "idempotency_key": "done"})
    app_env.db.update_production_job(done["id"], {"status": "succeeded"})
    def request(url, key, **kwargs):
        if kwargs.get("payload") is not None:
            return {"data": {"task_id": "cancel-me"}}
        app_env.productions.cancel(pid)
        return {"data": {"status": "completed", "video_url": "https://fake.invalid/clip.mp4"}}
    monkeypatch.setattr(providers, "_request_seedance", request)
    monkeypatch.setattr(providers, "_download_seedance_video", lambda *a: pytest.fail("cancelled job downloaded"))
    with pytest.raises(Exception):
        app_env.productions.engine._stage_generate(pid, cancel, [])
    jobs = app_env.db.list_production_jobs(pid)
    assert {j["tool"]: j["status"] for j in jobs} == {"test": "succeeded", "generate_video_shots": "cancelled"}
    assert app_env.db.get_production(pid)["status"] == "cancelled"
    job = next(j for j in jobs if j["tool"] == "generate_video_shots")
    app_env.db.update_production_job(job["id"], {"status": "succeeded"})
    assert app_env.db.get_production_job(job["id"])["status"] == "cancelled"


def test_recovery_continues_interrupted_step_without_ingest(app_env, monkeypatch):
    pid = ready(app_env)
    fake_local(app_env, monkeypatch)
    names = ["generate_video_shots", "separate_audio", "compose_timeline", "burn_subtitles"]
    app_env.db.update_production(pid, {"agent_trace": [
        {"type": "plan", "steps": names},
        {"type": "tool", "name": names[0], "success": True},
        {"type": "tool", "name": names[1], "success": True},
    ]})
    original_compose = app_env.productions.engine._stage_compose
    def interrupted(*args):
        original_compose(*args)
        raise Crash()
    monkeypatch.setattr(app_env.productions.engine, "_stage_compose", interrupted)
    with pytest.raises(Crash):
        host(app_env).compose_timeline({"production_id": pid}, {})
    job = app_env.db.list_production_jobs(pid)[0]
    assert job["status"] == "running"
    monkeypatch.setattr(app_env.productions.engine, "_stage_compose", original_compose)
    assert app_env.productions.recover_orphaned_runs(wait=True) == [pid]
    p = app_env.db.get_production(pid)
    assert p["status"] == "completed"
    last_plan = max(i for i, e in enumerate(p["agent_trace"]) if e["type"] == "plan")
    assert [e["name"] for e in p["agent_trace"][last_plan:] if e["type"] == "tool"] == names[2:]
    assert app_env.db.get_production_job(job["id"])["status"] == "succeeded"
    assert app_env.db.get_production_job(job["id"])["attempt_count"] == 2


def test_startup_recovers_remote_then_remaining_plan(app_env, monkeypatch):
    pid = remote_env(app_env, monkeypatch)
    fake_local(app_env, monkeypatch)
    names = ["generate_video_shots", "compose_timeline", "burn_subtitles"]
    set_plan(app_env, names)
    submits = []
    def request(url, key, **kwargs):
        if kwargs.get("payload") is not None:
            submits.append(1)
            return {"data": {"task_id": "startup-remote"}}
        assert app_env.db.list_production_jobs(pid)[0]["external_task_id"] == "startup-remote"
        raise Crash()
    monkeypatch.setattr(providers, "_request_seedance", request)
    with pytest.raises(Crash):
        app_env.productions.agent.run(pid)
    job_id = app_env.db.list_production_jobs(pid)[0]["id"]
    def poll(url, key, **kwargs):
        assert kwargs.get("payload") is None
        assert "startup-remote" in url
        assert app_env.db.get_production_job(job_id)["status"] == "running"
        return {"data": {"status": "completed", "result": {"video": {"url": "https://fake.invalid/video"}}}}
    monkeypatch.setattr(providers, "_request_seedance", poll)
    monkeypatch.setattr(providers, "_download_seedance_video", lambda url, dest, *a: dest.write_bytes(b"x" * 2000))
    assert app_env.productions.recover_orphaned_runs(wait=True) == [pid]
    assert app_env.db.get_production(pid)["status"] == "completed"
    assert len(submits) == 1
    assert all(j["status"] == "succeeded" for j in app_env.db.list_production_jobs(pid))


@pytest.mark.parametrize("status", ["pending", "running", "failed", "cancelled"])
def test_related_unfinished_job_blocks_completion(app_env, monkeypatch, status):
    from video_fission.jobs import revision
    pid = ready(app_env)
    fake_local(app_env, monkeypatch)
    h = host(app_env)
    h.compose_timeline({"production_id": pid}, {})
    h.burn_subtitles({"production_id": pid}, {})
    job = app_env.db.create_production_job({
        "production_id": pid, "tool": "generate_video_shots", "shot_id": "shot_1",
        "idempotency_key": "unfinished", "input_json": {"revision": revision(app_env.db.get_production(pid))},
    })
    app_env.db.update_production_job(job["id"], {"status": status})
    names = ["compose_timeline", "burn_subtitles"]
    app_env.productions.agent._finalize(pid, [{"tool": n} for n in names], names, threading.Event())
    assert app_env.db.get_production(pid)["status"] == ("cancelled" if status == "cancelled" else "failed")


def test_compose_key_ignores_mutable_audio_progress(app_env, monkeypatch):
    pid = ready(app_env)
    fake_local(app_env, monkeypatch)
    app_env.db.update_production(pid, {"audio_separation": {"bgm_path": "test.wav", "usable": True, "applied_to_output": False}})
    h = host(app_env)
    first = h.compose_timeline({"production_id": pid}, {})
    app_env.db.update_production(pid, {"audio_separation": {"bgm_path": "test.wav", "usable": True, "applied_to_output": True, "applied_volume": 0.12}})
    second = h.compose_timeline({"production_id": pid}, {})
    assert first["video_path"] == second["video_path"]
    assert len(app_env.db.list_production_jobs(pid)) == 1


def test_default_plan_keeps_required_subtitles(app_env):
    from video_fission.production_agent import DeterministicPlanner
    assert [step["tool"] for step in DeterministicPlanner().plan({"resume_from": "generate"})["steps"]] == [
        "generate_video_shots", "separate_audio", "compose_timeline", "burn_subtitles"]
