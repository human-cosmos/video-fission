from __future__ import annotations

import json
import time
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.helpers import finish_production
from video_fission.audio_separate import separate_reference_audio
from video_fission.container import configure
from video_fission.landing import analyze_landing, assert_public_url, extract_urls
from video_fission.main import app
from video_fission.media import audio_levels, ffmpeg_bin, probe, probe_audio
from video_fission.pipeline import PipelineEngine, _has_audio, _vocals_stem_usable
from video_fission.providers import (
    build_reference_input_contents,
    build_seedance_submit_payload,
    public_media_url,
)
from video_fission.script_flow import classify_followup, parse_script_selection
from video_fission.subtitle_style import claims_reference_aligned, default_safe_style, detect_subtitle_style

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample-reference.mp4"


@pytest.fixture
def client(test_settings):
    configure(test_settings)
    with TestClient(app) as test_client:
        yield test_client


def _upload(client: TestClient, path: Path = FIXTURE, name: str = "sample-reference.mp4") -> dict:
    with path.open("rb") as handle:
        response = client.post(
            "/api/productions/uploads",
            files={"file": (name, handle, "video/mp4")},
        )
    assert response.status_code == 201, response.text
    return response.json()


def _create(client: TestClient, brief: str, upload: dict | None = None) -> dict:
    uploaded = upload or _upload(client)
    created = client.post(
        "/api/productions",
        json={"brief": brief, "video_upload_id": uploaded["id"]},
    )
    assert created.status_code == 201, created.text
    return created.json()


def _run_ffmpeg(args: list[str]) -> None:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr[-1500:] or result.stdout[-1500:])


def _color_clip(path: Path, duration: float = 2.0, audio: str | None = "stereo") -> Path:
    args = [ffmpeg_bin(), "-y", "-f", "lavfi", "-i", f"color=c=0x1850A0:s=640x360:d={duration}:r=25"]
    if audio == "stereo":
        args.extend(
            [
                "-f", "lavfi", "-i", f"sine=frequency=1000:sample_rate=44100:duration={duration}",
                "-f", "lavfi", "-i", f"sine=frequency=200:sample_rate=44100:duration={duration}",
                "-filter_complex", "[1:a][2:a]join=inputs=2:channel_layout=stereo[a]",
                "-map", "0:v", "-map", "[a]",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                str(path),
            ]
        )
    else:
        args.extend(["-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)])
    _run_ffmpeg(args)
    return path


def test_extract_url_and_ssrf_is_rejected():
    assert extract_urls("看这个 https://shop.example.com/item?id=1 ，再做视频") == [
        "https://shop.example.com/item?id=1"
    ]
    for url in (
        "http://127.0.0.1/x",
        "http://localhost/x",
        "http://10.0.0.8/p",
        "http://169.254.169.254/latest/meta-data",
        "http://metadata.google.internal/",
        "file:///etc/passwd",
    ):
        with pytest.raises(ValueError, match="SSRF|只允许"):
            assert_public_url(url)


def test_static_html_extracts_selling_points_via_injected_fetch():
    html = """
    <html><head><title>轻薄手机壳</title>
    <meta name="description" content="不卡纹，手感好">
    </head><body>
    <h1>轻薄手机壳</h1>
    <h2>超薄 0.4mm</h2>
    <ul><li>不卡纹</li><li>防摔气囊</li></ul>
    <p>下单即送贴膜</p>
    </body></html>
    """

    def fetch(url: str):
        assert url.startswith("https://shop.example.com")
        return html, url

    result = analyze_landing("请根据 https://shop.example.com/p 做一条短视频", fetch=fetch)
    assert result is not None
    assert result["parse_status"] == "ok"
    assert result["source_url"].startswith("https://shop.example.com")
    assert result["product"]
    assert any("不卡纹" in item for item in result["selling_points"])
    assert result["method"] == "local_extract"


def test_analyze_landing_without_url_returns_none():
    assert analyze_landing("用香酥虾做一条短视频") is None


def test_script_pauses_for_choice_then_select_continues(client: TestClient, test_settings):
    created = _create(client, "用香酥虾做一个开场抓人、中间展示产品、结尾促单的短视频")
    from video_fission import container

    paused = container.productions.run(created["id"], wait=True)
    assert paused["status"] == "awaiting_script_choice"
    candidates = paused.get("script_candidates") or []
    assert len(candidates) == 3
    assert [item["id"] for item in candidates] == ["script_a", "script_b", "script_c"]
    assert len({item["title"] for item in candidates}) == 3
    assert len({item.get("angle") or "" for item in candidates}) == 3
    timelines = []
    openings = []
    voiceovers = []
    for item in candidates:
        shots = (item.get("script") or {}).get("shots") or []
        timelines.append(tuple((s.get("id"), s.get("start_ms"), s.get("end_ms"), s.get("duration_ms"), s.get("action"), s.get("source_segment_id")) for s in shots))
        openings.append((item.get("script") or {}).get("opening"))
        voiceovers.append(tuple(s.get("voiceover") for s in shots))
    assert len(set(timelines)) == 1
    assert openings[0]
    assert len(set(map(json.dumps, openings))) == 1
    assert len(set(voiceovers)) == 3
    assert not paused.get("clips")
    assert not (paused.get("output") or {}).get("video_path")
    art = Path(test_settings.artifacts_dir) / "productions" / created["id"]
    assert not (art / "final.mp4").exists()

    selected = client.post(
        f"/api/productions/{created['id']}/select-script",
        json={"script_id": "script_b"},
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()["selected_script_id"] == "script_b"
    finished = selected.json()
    deadline = time.time() + 180
    while finished["status"] == "running" and time.time() < deadline:
        time.sleep(0.4)
        finished = client.get(f"/api/productions/{created['id']}").json()
    assert finished["status"] == "completed", finished.get("error")
    assert finished["selected_script_id"] == "script_b"
    assert Path(finished["output"]["video_path"]).is_file()
    assert (art / "reference-vocals.m4a").is_file()
    assert (art / "reference-bgm.m4a").is_file()
    assert (art / "with-bgm.mp4").is_file()
    assert not (art / "reference-audio.m4a").exists()
    style = finished.get("subtitle_style") or {}
    assert style.get("method") in {"default_safe", "soft_sub"}
    if style.get("method") == "default_safe":
        assert style.get("aligned") is False
        assert "已对齐参考" not in str(style.get("note") or "")
        assert claims_reference_aligned(style) is False

    from video_fission.conversation_cards import plan_payload
    paused_plan = plan_payload({**paused, "status": "awaiting_script_choice"})
    assert paused_plan is not None
    assert paused_plan["status"] == "awaiting_script_choice"
    assert len(paused_plan.get("script_candidates") or []) == 3


def test_followup_classification_and_resume_from(client: TestClient):
    created = _create(client, "用香酥虾做一条短视频")
    from video_fission import container

    paused = container.productions.run(created["id"], wait=True)
    assert paused["status"] == "awaiting_script_choice"
    assert parse_script_selection("用第 2 个", paused["script_candidates"]) == "script_b"
    assert classify_followup(paused, "用第 2 个") == ("select", "script_b")
    assert classify_followup(paused, "换脚本，再出三个") == ("resume", "script")
    assert classify_followup(paused, "改卖点 https://shop.example.com/p")[0] == "resume"

    with pytest.raises(ValueError, match="选定脚本"):
        container.productions.run(created["id"], wait=True, resume_from="generate")
    with pytest.raises(ValueError, match="resume_from"):
        container.productions.run(created["id"], wait=True, resume_from="compose")
    with pytest.raises(ValueError, match="resume_from"):
        container.productions.engine.run(created["id"], resume_from="other")

    selected = container.productions.select_script(created["id"], "script_a", wait=True)
    assert selected["status"] == "completed", selected.get("error")
    assert classify_followup(selected, "再生成视频，改画面") == ("resume", "generate")
    assert classify_followup(selected, "换脚本") == ("resume", "script")

    rerun = container.productions.run(created["id"], wait=True, resume_from="script")
    assert rerun["status"] == "awaiting_script_choice"
    assert not rerun.get("selected_script_id")
    assert len(rerun.get("script_candidates") or []) == 3
    assert rerun["current_stage"] == "script"


def test_resume_from_generate_rejected_before_script(client: TestClient):
    created = _create(client, "用香酥虾做一条短视频")
    from video_fission import container

    with pytest.raises(ValueError, match="拆解|选定脚本"):
        container.productions.run(created["id"], wait=True, resume_from="generate")


def test_audio_stems_differ_from_mix_and_each_other(tmp_path: Path):
    source = _color_clip(tmp_path / "mix.mp4", duration=3.0, audio="stereo")
    result = separate_reference_audio(source, tmp_path / "stems")
    assert result["usable"] is True
    vocals = Path(result["vocals_path"])
    bgm = Path(result["bgm_path"])
    assert vocals.is_file() and bgm.is_file()
    src_ms = probe(source)["duration_ms"]
    v = probe_audio(vocals)
    b = probe_audio(bgm)
    assert abs(v["duration_ms"] - src_ms) <= 250
    assert abs(b["duration_ms"] - src_ms) <= 250
    assert vocals.read_bytes() != bgm.read_bytes()
    mix_audio = tmp_path / "mix-audio.m4a"
    from video_fission import media as media_mod

    media_mod.extract_audio(source, mix_audio)
    mix_bytes = mix_audio.read_bytes()
    assert vocals.read_bytes() != mix_bytes
    assert bgm.read_bytes() != mix_bytes


def test_seedance_request_body_includes_reference_av_and_guardrails():
    contents = build_reference_input_contents(
        "https://cdn.example.test/src.mp4",
        "https://cdn.example.test/reference-vocals.m4a",
        "https://cdn.example.test/reference-bgm.m4a",
    )
    prompt = PipelineEngine._shot_generate_prompt(
        type("E", (), {"_landing_prompt": lambda self, production: "卖点：不卡纹"})(),
        {"voiceover": "还在为壳卡纹发愁吗？", "visual": "特写手机壳", "prompt": "特写手机壳"},
        {"brief": "做一条短视频", "landing_page": {"selling_points": ["不卡纹"], "parse_status": "ok"}},
    )
    payload = build_seedance_submit_payload(
        "seedance-2.0-flash",
        prompt,
        2700,
        1080,
        1920,
        source="video-fission-test",
        resolution="720p",
        input_contents=contents,
    )
    assert payload["options"]["generate_audio"] is True
    roles = {(item["type"], item["role"]) for item in payload["input_contents"]}
    assert ("video_url", "reference_video") in roles
    assert sum(1 for item in payload["input_contents"] if item["role"] == "reference_audio") == 2
    assert "必须生成与口播一致的人声音轨" in payload["prompt"]
    assert "还在为壳卡纹发愁吗？" in payload["prompt"]
    assert "不得烧录" in payload["prompt"]
    empty_url, warning = public_media_url("", "prd_test", "source.mp4")
    assert empty_url == ""
    assert "公网" in warning
    local_url, local_warn = public_media_url("http://127.0.0.1:8000", "prd_test", "source.mp4")
    assert local_url.startswith("http://127.0.0.1:8000/")
    assert local_warn


def test_placeholder_stems_are_not_used_as_voiceover(tmp_path: Path, monkeypatch):
    silent = _color_clip(tmp_path / "silent.mp4", duration=1.5, audio=None)
    assert probe(silent)["has_audio"] is False
    sep = separate_reference_audio(silent, tmp_path)
    assert sep["usable"] is False
    assert sep["method"] == "placeholder"
    vocals = Path(sep["vocals_path"])
    assert vocals.is_file()
    assert _vocals_stem_usable(sep, vocals) is False

    dest = tmp_path / "shot.mp4"
    dest.write_bytes(silent.read_bytes())
    warnings: list[str] = []
    calls: list[str] = []

    def fake_tts(*_args, **_kwargs):
        calls.append("tts")
        speech = _kwargs.get("dest") or _args[-1]
        _run_ffmpeg(
            [
                ffmpeg_bin(),
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=1:sample_rate=44100",
                "-c:a",
                "libmp3lame",
                str(speech),
            ]
        )
        return speech

    from video_fission import providers

    monkeypatch.setattr(providers, "synthesize_speech", fake_tts)

    class Engine:
        def _emit(self, *_args, **_kwargs):
            return None

    production = {
        "id": "prd_silent",
        "audio_separation": sep,
    }
    public = {
        "tts": {
            "provider": "openai_compatible",
            "base_url": "https://tts.example.test",
            "model": "demo",
            "voice": "alloy",
            "timeout_seconds": 10,
        }
    }
    secrets = {"tts": "sk-test"}
    PipelineEngine._apply_voiceover(
        Engine(),  # type: ignore[arg-type]
        dest,
        {"id": "shot_01", "voiceover": "你好，这是口播。"},
        production,
        tmp_path,
        public,
        secrets,
        warnings,
        1500,
        0,
    )
    assert calls == ["tts"]
    assert _has_audio(dest)
    levels = audio_levels(dest)
    # TTS path must not be near-silent placeholder.
    assert levels.get("mean_db", -99) > -50 or levels.get("max_db", -99) > -30

    dest2 = tmp_path / "shot2.mp4"
    dest2.write_bytes(silent.read_bytes())
    warnings2: list[str] = []
    PipelineEngine._apply_voiceover(
        Engine(),  # type: ignore[arg-type]
        dest2,
        {"id": "shot_02", "voiceover": "你好，这是口播。"},
        production,
        tmp_path,
        {"tts": {"provider": "none"}},
        {},
        warnings2,
        1500,
        0,
    )
    assert any("没有可用口播" in item and "未写入静音轨" in item for item in warnings2)
    assert _has_audio(dest2) is False


def test_soft_sub_and_default_safe_styles(tmp_path: Path):
    plain = _color_clip(tmp_path / "plain.mp4", duration=1.2, audio=None)
    style = detect_subtitle_style(plain, tmp_path)
    assert style["method"] == "default_safe"
    assert style["aligned"] is False
    assert "已对齐参考" not in style["note"]
    assert claims_reference_aligned(style) is False
    assert claims_reference_aligned(default_safe_style()) is False

    ass = tmp_path / "ref.ass"
    ass.write_text(
        """[Script Info]
ScriptType: v4.00+
PlayResX: 640
PlayResY: 360

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Droid Sans Fallback,28,&H00FFFF00,&H000000FF,&H00111111,&H80000000,0,0,0,0,100,100,0,0,1,2,1,8,20,20,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,开场字幕
""",
        encoding="utf-8",
    )
    with_sub = tmp_path / "with-sub.mkv"
    _run_ffmpeg(
        [
            ffmpeg_bin(),
            "-y",
            "-i",
            str(plain),
            "-i",
            str(ass),
            "-c",
            "copy",
            str(with_sub),
        ]
    )
    detected = detect_subtitle_style(with_sub, tmp_path / "sub-out")
    assert detected["method"] == "soft_sub"
    assert detected["aligned"] is True
    assert "已对齐参考字幕" not in str(detected.get("note") or "")
    assert detected.get("alignment") == 8
    assert detected.get("margin_v") == 40
