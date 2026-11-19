from __future__ import annotations

from pathlib import Path
from typing import Any

VIDEO_CARD_TYPES = (
    "video_progress",
    "video_understanding",
    "video_plan",
    "video_segments",
    "video_observe",
    "video_output",
)

PRODUCTION_STAGES = (
    ("ingest", "接入", "读取参考视频和媒体信息"),
    ("decompose", "拆解", "识别转场、镜头和画面内容"),
    ("intent", "意图", "提炼目标、受众和核心承诺"),
    ("script", "脚本", "生成候选创意与分镜脚本"),
    ("generate", "生成", "生成或复用每个镜头片段"),
    ("compose", "合成", "拼接模型生成与复用镜头并输出成片"),
    ("done", "完成", "成片已交付"),
)

STEP_LABELS = {
    "transition": "场景转场",
    "understand": "整片理解",
    "refine": "切片校正",
    "clip_analysis": "关键帧与片段",
    "observe": "片段观察",
    "assemble": "片段拼接",
    "finalize": "音画对齐",
}

DETAIL_LABELS = {
    "segments": "切片",
    "cuts": "转场切点",
    "tags": "标签",
    "provider": "方式",
    "model": "模型",
    "clips": "片段",
    "duration_ms": "耗时",
    "resolution": "分辨率",
    "fps": "帧率",
    "file": "文件",
    "observed": "已观察",
    "failed": "失败",
}


def stable_item_id(production_id: str, item_type: str) -> str:
    suffix = item_type.removeprefix("video_")
    return f"{production_id}:{suffix}"


def upload_media_url(upload_id: str | None) -> str:
    if not upload_id:
        return ""
    return f"/api/productions/uploads/{upload_id}"


def artifact_url(production_id: str, name: str) -> str:
    return f"/api/productions/{production_id}/artifacts/{name}"


def _upload_id(public: dict[str, Any]) -> str:
    source = public.get("source_video") or {}
    return str(source.get("upload_id") or public.get("video_upload_id") or "")


def _stage_index(stage_id: str) -> int:
    for index, (candidate, _label, _description) in enumerate(PRODUCTION_STAGES):
        if candidate == stage_id:
            return index
    return -1


def _stage_state(public: dict[str, Any], stage_id: str) -> str:
    status = str(public.get("status") or "draft")
    current = str(public.get("current_stage") or "ingest")
    index = _stage_index(stage_id)
    current_index = _stage_index(current)
    if index < 0:
        return "pending"
    if status == "completed" or current == "done":
        return "done"
    if status == "awaiting_script_choice":
        script_index = _stage_index("script")
        if index < script_index:
            return "done"
        if index == script_index:
            return "awaiting"
        return "pending"
    if status == "awaiting_video_choice":
        compose_index = _stage_index("compose")
        if index < compose_index:
            return "done"
        if index == compose_index:
            return "awaiting"
        return "pending"
    if status == "awaiting_creative_context":
        intent_index = _stage_index("intent")
        if index < intent_index:
            return "done"
        if index == intent_index:
            return "awaiting"
        return "pending"
    if status in {"failed", "cancelled"}:
        if current_index < 0:
            return "pending"
        if index < current_index:
            return "done"
        if index == current_index:
            return status
        return "pending"
    if status == "running" and current_index >= 0:
        if index < current_index:
            return "done"
        if index == current_index:
            return "active"
        return "pending"
    return "pending"


def _format_duration(ms: Any) -> str:
    try:
        value = max(0, int(ms))
    except (TypeError, ValueError):
        return ""
    if value < 1000:
        return f"{value} ms"
    seconds = value / 1000
    return f"{seconds:.1f} 秒" if value % 1000 else f"{int(seconds)} 秒"


def _short_text(value: Any, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)]}…"


def _resolution_text(source: dict[str, Any]) -> str:
    try:
        width = int(source.get("width") or 0)
        height = int(source.get("height") or 0)
    except (TypeError, ValueError):
        return ""
    return f"{width} × {height}" if width > 0 and height > 0 else ""


def _role_counts(segments: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for segment in segments:
        role = str(segment.get("role") or "other")
        counts[role] = counts.get(role, 0) + 1
    return " / ".join(
        f"{role} {counts[role]}"
        for role in ("hook", "value", "cta", "other")
        if counts.get(role)
    )


def _output_row(label: str, value: Any) -> dict[str, str] | None:
    text = _short_text(value, 180)
    return {"label": label, "value": text} if text else None


def _stage_outputs(public: dict[str, Any], stage_id: str, state: str) -> list[dict[str, str]]:
    if state == "pending":
        return []
    rows: list[dict[str, str] | None] = []
    source = public.get("source_video") if isinstance(public.get("source_video"), dict) else {}
    decomposition = (
        public.get("decomposition")
        if isinstance(public.get("decomposition"), dict)
        else {}
    )
    segments = decomposition.get("segments") or []
    intent = public.get("intent") if isinstance(public.get("intent"), dict) else {}
    skeleton = public.get("skeleton") if isinstance(public.get("skeleton"), dict) else {}

    if stage_id == "ingest" and source:
        filename = str(
            source.get("filename") or Path(str(source.get("path") or "")).name or ""
        )
        fps = source.get("fps")
        rows.extend(
            [
                _output_row("文件", filename),
                _output_row("时长", _format_duration(source.get("duration_ms"))),
                _output_row("分辨率", _resolution_text(source)),
                _output_row("帧率", f"{float(fps):.2f} fps" if fps else ""),
            ]
        )
    elif stage_id == "decompose" and decomposition:
        tags = (
            decomposition.get("video_tags")
            if isinstance(decomposition.get("video_tags"), dict)
            else {}
        )
        tag_count = sum(1 for value in tags.values() if value not in (None, "", [], {}))
        opening = (
            decomposition.get("opening")
            if isinstance(decomposition.get("opening"), dict)
            else {}
        )
        rows.extend(
            [
                _output_row(
                    "整片摘要",
                    decomposition.get("summary") or decomposition.get("description"),
                ),
                _output_row(
                    "切片结果",
                    f"{len(segments)} 段 · {_role_counts(segments)}" if segments else "",
                ),
                _output_row(
                    "理解方式",
                    " / ".join(
                        filter(
                            None,
                            [
                                decomposition.get("provider"),
                                decomposition.get("model"),
                            ],
                        )
                    ),
                ),
                _output_row("视频标签", f"{tag_count} 类" if tag_count else ""),
                _output_row(
                    "开头区间",
                    f"0–{_format_duration(opening.get('end_ms'))}"
                    if opening.get("end_ms")
                    else "",
                ),
            ]
        )
    elif stage_id == "intent":
        rows.extend(
            [
                _output_row("目标", intent.get("goal")),
                _output_row("受众", intent.get("audience")),
                _output_row("核心承诺", intent.get("promise")),
                _output_row("一句话骨架", skeleton.get("one_liner")),
                _output_row(
                    "Hook / Value / CTA",
                    " / ".join(
                        filter(
                            None,
                            [
                                skeleton.get("hook"),
                                skeleton.get("value"),
                                skeleton.get("cta"),
                            ],
                        )
                    ),
                ),
            ]
        )
    elif stage_id == "script":
        candidates = public.get("script_candidates") or []
        selected_id = str(public.get("selected_script_id") or "")
        selected = next(
            (item for item in candidates if str(item.get("id") or "") == selected_id),
            None,
        )
        chosen_script = (
            (selected or {}).get("script")
            if isinstance(selected, dict)
            else public.get("script")
        )
        shots = (chosen_script or {}).get("shots") or []
        titles = [
            str(item.get("title") or item.get("angle") or item.get("id") or "")
            for item in candidates
        ]
        rows.extend(
            [
                _output_row("候选脚本", f"{len(candidates)} 份" if candidates else ""),
                _output_row("脚本角度", " / ".join(title for title in titles if title)),
                _output_row(
                    "当前选用",
                    (selected or {}).get("title") if selected else "等待选择",
                ),
                _output_row("分镜结果", f"{len(shots)} 个镜头" if shots else ""),
                _output_row(
                    "脚本时长",
                    _format_duration((chosen_script or {}).get("total_duration_ms")),
                ),
            ]
        )
    elif stage_id == "generate":
        candidate_videos = public.get("candidate_videos") or []
        clips = public.get("clips") or []
        script = public.get("script") if isinstance(public.get("script"), dict) else {}
        shots = script.get("shots") or []
        actions = {
            str(shot.get("id") or ""): str(shot.get("action") or "")
            for shot in shots
        }
        generated = sum(
            1
            for clip in clips
            if actions.get(str(clip.get("shot_id") or "")) == "generate"
        )
        reused = sum(
            1
            for clip in clips
            if actions.get(str(clip.get("shot_id") or "")) == "reuse"
        )
        providers = sorted(
            {str(clip.get("provider") or "") for clip in clips if clip.get("provider")}
        )
        rows.extend(
            [
                _output_row(
                    "候选视频",
                    f"{len(candidate_videos)} / 3 条已输出" if candidate_videos else "",
                ),
                _output_row(
                    "片段结果",
                    f"{len(clips)} / {len(shots)} 个镜头已输出" if clips else "",
                ),
                _output_row(
                    "生成方式",
                    f"新生成 {generated} · 复用 {reused}" if clips else "",
                ),
                _output_row("执行通道", " / ".join(providers)),
            ]
        )
    elif stage_id == "compose":
        output = public.get("output") if isinstance(public.get("output"), dict) else {}
        script = public.get("script") if isinstance(public.get("script"), dict) else {}
        video_path = Path(str(output.get("video_path") or ""))
        try:
            size_text = (
                f"{video_path.stat().st_size / (1024 * 1024):.1f} MB"
                if video_path.is_file()
                else ""
            )
        except OSError:
            size_text = ""
        rows.extend(
            [
                _output_row(
                    "成片时长",
                    _format_duration(script.get("total_duration_ms")),
                ),
                _output_row(
                    "成片文件",
                    f"{video_path.name} · {size_text}" if video_path.name else "",
                ),
                _output_row("输出地址", output.get("video_url")),
            ]
        )
    elif stage_id == "done" and public.get("status") == "completed":
        rows.append(_output_row("交付状态", "成片已生成"))
    return [row for row in rows if row]


def _detail_text(details: Any) -> str:
    if not isinstance(details, dict):
        return ""
    parts: list[str] = []
    for key, value in details.items():
        if value in (None, "", [], {}):
            continue
        label = DETAIL_LABELS.get(str(key), str(key))
        if str(key).endswith("_ms"):
            text = _format_duration(value)
        elif isinstance(value, list):
            text = "、".join(str(item) for item in value[:6])
        else:
            text = str(value)
        if text:
            parts.append(f"{label} {text}")
    return " · ".join(parts[:5])


def _activity_item(
    raw: dict[str, Any],
    fallback_stage: str = "",
) -> dict[str, Any] | None:
    payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
    kind = str(raw.get("type") or raw.get("kind") or "")
    raw_stage = str(payload.get("stage") or fallback_stage or "")
    cancelled = raw_stage == "cancelled"
    stage = raw_stage
    if cancelled:
        stage = fallback_stage
    step = str(payload.get("step") or "")
    message = _short_text(payload.get("message") or payload.get("summary") or "", 220)
    stage_label = next(
        (label for ident, label, _ in PRODUCTION_STAGES if ident == stage),
        stage or "任务",
    )
    step_label = STEP_LABELS.get(step, step)
    if kind == "stage_started":
        text = message or f"开始{step_label or stage_label}"
    elif kind == "stage_completed":
        text = message or f"{step_label or stage_label}完成"
    elif kind == "warning":
        text = message or f"{step_label or stage_label}出现提示"
    elif kind == "error":
        if cancelled or message == "已取消":
            text = "任务已取消"
        else:
            text = f"执行失败：{message}" if message else "执行失败"
    elif kind == "awaiting_script_choice":
        text = message or "等待选择创意脚本"
    elif kind == "awaiting_video_choice":
        text = message or "等待选择候选视频"
    elif kind == "completed":
        text = "全部步骤完成，成片已生成"
    else:
        text = message or kind.replace("_", " ")
    detail = _detail_text(payload.get("details"))
    if not stage and not text:
        return None
    if text == "任务已取消":
        tone = "warning"
    elif kind == "error":
        tone = "error"
    elif kind == "warning":
        tone = "warning"
    elif kind == "stage_started":
        tone = "active"
    else:
        tone = "done"
    return {
        "id": raw.get("id"),
        "stage": stage,
        "step": step,
        "kind": kind,
        "tone": tone,
        "text": text,
        "detail": detail,
        "created_at": raw.get("created_at"),
    }


def _activity_items(
    events: list[dict[str, Any]],
    current_stage: str,
) -> list[dict[str, Any]]:
    items = []
    for raw in events:
        item = _activity_item(raw, fallback_stage=current_stage)
        if item:
            items.append(item)
    return items


def progress_payload(
    public: dict[str, Any],
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    status = public.get("status") or "draft"
    activity = _activity_items(events or [], str(public.get("current_stage") or ""))
    stages = []
    for stage_id, label, description in PRODUCTION_STAGES:
        state = _stage_state(public, stage_id)
        outputs = _stage_outputs(public, stage_id, state)
        stage_activity = [item for item in activity if item.get("stage") == stage_id]
        stages.append(
            {
                "id": stage_id,
                "label": label,
                "description": description,
                "state": state,
                "summary": outputs[0]["value"] if outputs else "",
                "outputs": outputs,
                "activities": stage_activity[-20:],
            }
        )
    return {
        "production_id": public["id"],
        "title": public.get("title") or "",
        "brief": public.get("brief") or "",
        "status": status,
        "current_stage": public.get("current_stage") or "",
        "updated_at": public.get("updated_at"),
        "warnings": public.get("warnings") or [],
        "error": public.get("error"),
        "cancellable": status == "running",
        "stages": stages,
        "activity": activity[-120:],
        "agent_trace": public.get("agent_trace") or [],
    }


def understanding_payload(public: dict[str, Any]) -> dict[str, Any] | None:
    decomposition = public.get("decomposition")
    if not isinstance(decomposition, dict) or not decomposition:
        return None
    source = dict(public.get("source_video") or {})
    source_url = upload_media_url(_upload_id(public))
    if source_url:
        source["url"] = source_url
    tags = decomposition.get("video_tags") if isinstance(decomposition.get("video_tags"), dict) else {}
    return {
        "production_id": public["id"],
        "summary": decomposition.get("summary") or "",
        "description": decomposition.get("description") or tags.get("video_describe") or "",
        "provider": decomposition.get("provider") or "",
        "model": decomposition.get("model") or "",
        "video_tags": tags,
        "segments": list(decomposition.get("segments") or []),
        "opening": decomposition.get("opening"),
        "source_video": source,
        "source_video_url": source_url,
    }


def _media_duration_ms(public: dict[str, Any]) -> int:
    source = public.get("source_video") or {}
    try:
        duration = int(source.get("duration_ms") or 0)
    except (TypeError, ValueError):
        duration = 0
    if duration > 0:
        return duration
    segments = (public.get("decomposition") or {}).get("segments") or []
    ends = []
    for segment in segments:
        try:
            ends.append(int(segment.get("end_ms") or 0))
        except (TypeError, ValueError):
            continue
    return max(ends) if ends else 0


def plan_payload(public: dict[str, Any]) -> dict[str, Any] | None:
    if not any(
        public.get(key)
        for key in ("intent", "skeleton", "script", "replace_plan", "script_candidates", "landing_page", "agent_trace")
    ):
        return None
    decomposition = public.get("decomposition") or {}
    duration_ms = _media_duration_ms(public)
    payload = {
        "production_id": public["id"],
        "status": public.get("status"),
        "current_stage": public.get("current_stage"),
        "intent": public.get("intent"),
        "skeleton": public.get("skeleton"),
        "script": public.get("script"),
        "replace_policy": public.get("replace_policy"),
        "replace_plan": public.get("replace_plan"),
        "opening": decomposition.get("opening") if isinstance(decomposition, dict) else None,
        "landing_page": public.get("landing_page"),
        "script_candidates": public.get("script_candidates") or [],
        "selected_script_id": public.get("selected_script_id"),
        "agent_trace": public.get("agent_trace") or [],
    }
    if duration_ms > 0:
        payload["duration_ms"] = duration_ms
    return payload


def _segment_action(segment_id: str, public: dict[str, Any]) -> str:
    clips = public.get("clips") or []
    for clip in clips:
        source = str(clip.get("source_segment_id") or clip.get("id") or "")
        if source == segment_id or clip.get("id") == segment_id:
            return str(clip.get("action") or "")
    plan = public.get("replace_plan") or {}
    resolved = set(plan.get("resolved_segment_ids") or [])
    if resolved:
        return "generate" if segment_id in resolved else "reuse"
    return ""


def _preview_url(production_id: str, segment_id: str, artifact_dir: Path | None) -> str:
    if not segment_id:
        return ""
    candidates = [f"{segment_id}_frame_02.jpg", f"{segment_id}_frame_01.jpg", f"{segment_id}_frame_03.jpg"]
    if artifact_dir is not None:
        for name in candidates:
            if (artifact_dir / name).is_file():
                return artifact_url(production_id, name)
        return ""
    return artifact_url(production_id, candidates[0])


def segments_payload(public: dict[str, Any], artifact_dir: Path | None = None) -> dict[str, Any] | None:
    decomposition = public.get("decomposition")
    if not isinstance(decomposition, dict) or not decomposition:
        return None
    production_id = public["id"]
    items = []
    for segment in decomposition.get("segments") or []:
        segment_id = str(segment.get("id") or "")
        items.append(
            {
                **segment,
                "action": _segment_action(segment_id, public),
                "preview_url": _preview_url(production_id, segment_id, artifact_dir),
            }
        )
    clips = []
    for clip in public.get("clips") or []:
        name = Path(str(clip.get("path") or "")).name
        preview = artifact_url(production_id, name) if name else ""
        clips.append({**clip, "preview_url": preview or clip.get("preview_url") or ""})
    return {
        "production_id": production_id,
        "status": public.get("status"),
        "current_stage": public.get("current_stage") or "",
        "updated_at": public.get("updated_at"),
        "segments": items,
        "clips": clips,
        "opening": decomposition.get("opening"),
        "replace_plan": public.get("replace_plan"),
        "replace_policy": public.get("replace_policy"),
    }


def output_payload(public: dict[str, Any]) -> dict[str, Any] | None:
    output = public.get("output")
    status = public.get("status")
    candidate_videos = list(public.get("candidate_videos") or [])
    if not output and not candidate_videos and status not in {"completed", "failed"}:
        return None
    return {
        "production_id": public["id"],
        "status": status,
        "error": public.get("error"),
        "output": output,
        "candidate_videos": candidate_videos,
        "selected_script_id": public.get("selected_script_id"),
        "video_url": (output or {}).get("video_url") if isinstance(output, dict) else "",
        "updated_at": public.get("updated_at"),
    }


def _observe_empty(observe: Any) -> bool:
    if not isinstance(observe, dict):
        return True
    if observe.get("shot_observation_card") or observe.get("raw_claims"):
        return False
    speech = observe.get("speech_evidence") or {}
    if isinstance(speech, dict) and (
        speech.get("segments") or str(speech.get("status") or "not_used") not in {"", "not_used"}
    ):
        return False
    return True


def observe_payload(public: dict[str, Any]) -> dict[str, Any] | None:
    decomposition = public.get("decomposition")
    if not isinstance(decomposition, dict) or not decomposition:
        return None
    items = []
    for segment in decomposition.get("segments") or []:
        observe = segment.get("observe")
        error = segment.get("error")
        if _observe_empty(observe) and not error:
            continue
        if _observe_empty(observe) and error and "observe" not in str(error):
            # 切片失败但没有观察结果时不单独占观察卡；仅 observe 失败才列出
            continue
        items.append(
            {
                "id": segment.get("id"),
                "role": segment.get("role"),
                "start_ms": segment.get("start_ms"),
                "end_ms": segment.get("end_ms"),
                "summary": segment.get("summary") or "",
                "observe": observe if isinstance(observe, dict) else {},
                "error": error,
            }
        )
    if not items:
        return None
    return {
        "production_id": public["id"],
        "status": public.get("status"),
        "updated_at": public.get("updated_at"),
        "segments": items,
    }


def cards_for(
    public: dict[str, Any],
    artifact_dir: Path | None = None,
    events: list[dict[str, Any]] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    cards: list[tuple[str, dict[str, Any]]] = [("video_progress", progress_payload(public, events))]
    understanding = understanding_payload(public)
    if understanding:
        cards.append(("video_understanding", understanding))
    plan = plan_payload(public)
    if plan:
        cards.append(("video_plan", plan))
    segments = segments_payload(public, artifact_dir)
    if segments:
        cards.append(("video_segments", segments))
    observe = observe_payload(public)
    if observe:
        cards.append(("video_observe", observe))
    output = output_payload(public)
    if output:
        cards.append(("video_output", output))
    return cards
