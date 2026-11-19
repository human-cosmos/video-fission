"""Run isolated Codex Agent turns for video intent and script generation."""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from .app_server import CodexAppServer, CodexAppServerError
from .config import Settings
from .db import Database
from .logging_utils import clip_text
from .paths import user_workspace
from .protocol import build_input, extract_item_id, flatten_text, text_delta
from .skills import SkillManager
from .understand.normalize import extract_json

logger = logging.getLogger(__name__)

VIDEO_CREATIVE_SKILLS = (
    "video-references",
    "creative-intent-script-skeleton",
    "universal-ugc-video-script-generation",
)


class ScriptAgentError(RuntimeError):
    pass


class VideoScriptAgent:
    def __init__(self, settings: Settings, db: Database, skills: SkillManager):
        self.settings = settings
        self.db = db
        self.skills = skills

    def generate_intent(
        self,
        production: dict[str, Any],
        refs: list[dict[str, Any]],
        *,
        cancel: threading.Event | None = None,
    ) -> dict[str, Any]:
        context = {
            "task": "intent_and_skeleton",
            "production_id": production.get("id"),
            "brief": production.get("brief"),
            "landing_page": production.get("landing_page"),
            "decomposition": self._compact_decomposition(production),
            "retrieved_references": [
                {
                    "path": str((self.settings.project_root / str(item.get("path") or "")).resolve()),
                    "why": item.get("why"),
                }
                for item in refs
            ],
        }
        prompt = (
            "请使用 skill://video-references 和 skill://creative-intent-script-skeleton 完成任务。"
            "先实际读取相关 Skill 和参考脚本，再结合下方 Gemini 视频拆解，产出创意意图与骨架。"
            "只返回一个严格 JSON 对象，不要 Markdown。\n"
            '结构：{"intent":{"goal":"","audience":"","promise":"","assumptions":[""]},'
            '"skeleton":{"one_liner":"","hook":"","value":"","cta":""}}。\n'
            "不得使用通用模板、不得编造商品事实。\n"
            f"上下文：{json.dumps(context, ensure_ascii=False)}"
        )
        return self._run_agent(
            production,
            task="intent",
            prompt=prompt,
            artifact_dir=Path(production["_artifact_dir"]),
            cancel=cancel,
        )

    def generate_script_candidates(
        self,
        production: dict[str, Any],
        timeline: dict[str, Any],
        reference_paths: list[str],
        *,
        cancel: threading.Event | None = None,
    ) -> dict[str, Any]:
        context = {
            "task": "three_script_candidates",
            "production_id": production.get("id"),
            "brief": production.get("brief"),
            "landing_page": production.get("landing_page"),
            "intent": production.get("intent"),
            "skeleton": production.get("skeleton"),
            "decomposition": self._compact_decomposition(production),
            "timeline": self._prompt_timeline(production, timeline),
            "reference_paths": [
                str((self.settings.project_root / path).resolve())
                for path in reference_paths
            ],
        }
        prompt = (
            "请使用 skill://video-references 和 "
            "skill://universal-ugc-video-script-generation 完成任务。"
            "必须实际读取至少 3 篇匹配的参考脚本，遵守 Skill 的复用/生成约束。"
            "根据 Gemini 视频拆解和锁定时间轴写恰好 3 个可直接进入视频生成与剪辑的候选。"
            "只返回一个严格 JSON 对象，不要 Markdown。\n"
            '结构：{"candidates":[{"id":"script_a","title":"","angle":"",'
            '"script":{"audio_plan":{"voiceover_language":"zh"},'
            '"shots":[{"id":"","visual":"","voiceover":"","on_screen_text":"","prompt":""}]}}]}。\n'
            "每个候选的 shots 数量、顺序、id、role、duration_ms、action、source_segment_id "
            "必须与 timeline 完全一致。不得使用模板文案，不得编造商品事实。\n"
            f"上下文：{json.dumps(context, ensure_ascii=False)}"
        )
        return self._run_agent(
            production,
            task="script",
            prompt=prompt,
            artifact_dir=Path(production["_artifact_dir"]),
            cancel=cancel,
        )

    def _run_agent(
        self,
        production: dict[str, Any],
        *,
        task: str,
        prompt: str,
        artifact_dir: Path,
        cancel: threading.Event | None,
    ) -> dict[str, Any]:
        if not self.settings.codex_bin:
            raise ScriptAgentError("Codex Agent 运行时不可用")
        cwd = str(user_workspace(self.settings.workspace, production.get("user_id")).resolve())
        done = threading.Event()
        state: dict[str, Any] = {"error": "", "messages": {}}
        conversation = self.db.get_conversation(
            str(production.get("conversation_id") or ""),
            production.get("user_id"),
        ) or {}

        def on_notification(method: str, params: dict[str, Any]) -> None:
            if method == "item/agentMessage/delta":
                item_id = extract_item_id(params) or "agent"
                messages = state["messages"]
                messages[item_id] = str(messages.get(item_id) or "") + text_delta(params)
            elif method == "item/completed":
                item = params.get("item") if isinstance(params.get("item"), dict) else params
                if str(item.get("type") or "") == "agentMessage":
                    item_id = extract_item_id(params) or "agent"
                    text = flatten_text(item)
                    if text:
                        state["messages"][item_id] = text
            elif method == "turn/completed":
                turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
                status = str(turn.get("status") or "completed")
                if status == "failed":
                    state["error"] = flatten_text(turn) or "Agent turn failed"
                done.set()
            elif method == "error":
                state["error"] = str(
                    params.get("message")
                    or (params.get("error") or {}).get("message")
                    or "Agent runtime error"
                )
                done.set()

        def on_server_request(message: dict[str, Any]) -> Any:
            method = str(message.get("method") or "")
            if method in {"item/skill/requestApproval", "skillApproval"}:
                return {"decision": "acceptForSession"}
            if method in {
                "item/commandExecution/requestApproval",
                "execCommandApproval",
            }:
                return {"decision": "acceptForSession"}
            if method in {"item/fileChange/requestApproval", "applyPatchApproval"}:
                return {"decision": "decline"}
            return {}

        runtime = CodexAppServer(
            self.settings.codex_bin,
            cwd=cwd,
            timeout_seconds=max(int(self.settings.script_agent_timeout_seconds), 60),
            on_notification=on_notification,
            on_server_request=on_server_request,
            on_exit=lambda _runtime, detail: (
                state.update(error=f"Agent runtime exited: {detail}"),
                done.set(),
            ),
        )
        try:
            runtime.start().initialize()
            try:
                runtime.set_skill_roots(self.skills.runtime_roots)
            except Exception:
                logger.warning("script_agent.skill_roots_unavailable", exc_info=True)
            thread_result = runtime.start_thread(
                {
                    "cwd": cwd,
                    "sandbox": "read-only",
                    "approvalPolicy": "never",
                    "ephemeral": True,
                    "historyMode": "paginated",
                    "model": conversation.get("model"),
                    "developerInstructions": (
                        "你是视频创作脚本 Agent。必须调用项目 Skills，"
                        "不得用自由模板代替真实 Skill 内容。工作区只读，"
                        "最终消息只能包含一个严格 JSON 对象。"
                    ),
                }
            )
            thread = thread_result.get("thread") or {}
            thread_id = str(thread.get("id") or "")
            if not thread_id:
                raise ScriptAgentError("Agent 未返回 thread id")
            mentions = [
                {"name": slug, "path": f"skill://{slug}"}
                for slug in VIDEO_CREATIVE_SKILLS
            ]
            turn_result = runtime.start_turn(
                {
                    "threadId": thread_id,
                    "cwd": cwd,
                    "input": build_input(prompt, mentions),
                    "model": conversation.get("model"),
                    "effort": conversation.get("reasoning_effort"),
                    "approvalPolicy": "never",
                    "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
                    "clientUserMessageId": f"video-script-{task}-{time.time_ns()}",
                }
            )
            turn = turn_result.get("turn") or {}
            if str(turn.get("status") or "") == "completed":
                done.set()
            deadline = time.monotonic() + max(int(self.settings.script_agent_timeout_seconds), 60)
            while not done.wait(0.25):
                if cancel is not None and cancel.is_set():
                    try:
                        if turn.get("id"):
                            runtime.interrupt(thread_id, str(turn["id"]))
                    finally:
                        raise ScriptAgentError("任务已取消")
                if time.monotonic() >= deadline:
                    raise ScriptAgentError(
                        f"Agent 脚本生成超过 {self.settings.script_agent_timeout_seconds} 秒"
                    )
                if not runtime.running:
                    raise ScriptAgentError(state.get("error") or "Agent 运行时已退出")
            if state.get("error"):
                raise ScriptAgentError(str(state["error"]))
            raw_text = "\n".join(
                str(text).strip()
                for text in state["messages"].values()
                if str(text).strip()
            ).strip()
            artifact_dir.mkdir(parents=True, exist_ok=True)
            output_path = artifact_dir / f"agent_{task}.txt"
            output_path.write_text(raw_text, encoding="utf-8")
            data = extract_json(raw_text)
            if not isinstance(data, dict):
                raise ScriptAgentError(
                    "Agent 未返回可解析 JSON：" + clip_text(raw_text or "<empty>", 500)
                )
            return data
        except CodexAppServerError as exc:
            raise ScriptAgentError(f"Agent 运行失败：{exc}") from exc
        finally:
            runtime.close()

    @staticmethod
    def _compact_decomposition(production: dict[str, Any]) -> dict[str, Any]:
        decomposition = production.get("decomposition") or {}
        return {
            "summary": decomposition.get("summary") or "",
            "description": decomposition.get("description") or "",
            "provider": decomposition.get("provider") or "",
            "model": decomposition.get("model") or "",
            "video_tags": decomposition.get("video_tags") or {},
            "opening": decomposition.get("opening") or {},
            "segments": [
                {
                    "id": item.get("id"),
                    "role": item.get("role"),
                    "start_ms": item.get("start_ms"),
                    "end_ms": item.get("end_ms"),
                    "summary": item.get("summary"),
                    "visual": item.get("visual"),
                    "audio": item.get("audio"),
                    "on_screen_text": item.get("on_screen_text"),
                    "observe": item.get("observe"),
                }
                for item in decomposition.get("segments") or []
            ],
        }

    @staticmethod
    def _prompt_timeline(production: dict[str, Any], timeline: dict[str, Any]) -> list[dict[str, Any]]:
        segments = {
            item.get("id"): item
            for item in (production.get("decomposition") or {}).get("segments") or []
        }
        return [
            {
                "id": shot.get("id"),
                "role": shot.get("role"),
                "duration_ms": shot.get("duration_ms"),
                "action": shot.get("action"),
                "source_segment_id": shot.get("source_segment_id"),
                "source_summary": (segments.get(shot.get("source_segment_id")) or {}).get("summary") or "",
                "source_visual": (segments.get(shot.get("source_segment_id")) or {}).get("visual") or "",
            }
            for shot in timeline.get("shots") or []
        ]
