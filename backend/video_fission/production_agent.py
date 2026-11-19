"""Task-level Production Agent: plan, load skills, call tools, pause at checkpoints."""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Protocol

from .app_server import CodexAppServer, CodexAppServerError
from .config import Settings
from .db import Database
from .logging_utils import clip_text
from .paths import user_workspace
from .production_tools import (
    CAPABILITY_TOOLS,
    FORBIDDEN_CLAIMS,
    ProductionToolError,
    contains_forbidden_claim,
)
from .protocol import build_input, extract_item_id, flatten_text, text_delta
from .skills import SkillManager
from .tools import ToolRegistry
from .understand.normalize import extract_json

logger = logging.getLogger(__name__)

ORCHESTRATION_SKILL = "production-orchestration"
VIDEO_CREATIVE_SKILLS = (
    "production-orchestration",
    "video-references",
    "creative-intent-script-skeleton",
    "universal-ugc-video-script-generation",
)
CODEX_UNAVAILABLE = "Codex 不可用，无法编排出片。请检查 Codex CLI 是否已安装并配置。"


class ProductionAgentError(RuntimeError):
    pass


def _prompt_context(context: dict[str, Any]) -> dict[str, Any]:
    """Strip runtime-only handles before serializing the planner prompt."""
    return {key: value for key, value in context.items() if key != "cancel"}


class AgentRuntime(Protocol):
    def is_available(self) -> bool: ...

    def plan(self, context: dict[str, Any]) -> dict[str, Any]: ...


class FakeCodexRuntime:
    """Injectable scripted planner for tests. Never starts a real `codex` process."""

    def __init__(self, plan: dict[str, Any] | None = None, *, available: bool = True):
        self._plan = plan
        self._available = available
        self.plans: list[dict[str, Any]] = []

    def is_available(self) -> bool:
        return self._available

    def plan(self, context: dict[str, Any]) -> dict[str, Any]:
        self.plans.append(context)
        if self._plan is not None:
            return dict(self._plan)
        raise ProductionAgentError("测试运行时没有提供编排计划")


class RealCodexRuntime:
    def __init__(self, settings: Settings, db: Database, skills: SkillManager):
        self.settings = settings
        self.db = db
        self.skills = skills

    def is_available(self) -> bool:
        return bool(self.settings.codex_bin)

    def plan(self, context: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.codex_bin:
            raise ProductionAgentError(CODEX_UNAVAILABLE)
        production = context.get("production") or {}
        cwd = str(user_workspace(self.settings.workspace, production.get("user_id")).resolve())
        conversation = self.db.get_conversation(
            str(production.get("conversation_id") or ""),
            production.get("user_id"),
        ) or {}
        done = threading.Event()
        state: dict[str, Any] = {"error": "", "messages": {}}

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
                if str(turn.get("status") or "completed") == "failed":
                    state["error"] = flatten_text(turn) or "Agent turn failed"
                done.set()
            elif method == "error":
                state["error"] = str(
                    params.get("message") or (params.get("error") or {}).get("message") or "Agent runtime error"
                )
                done.set()

        def on_server_request(message: dict[str, Any]) -> Any:
            method = str(message.get("method") or "")
            if method in {"item/skill/requestApproval", "skillApproval"}:
                return {"decision": "acceptForSession"}
            if method in {"item/commandExecution/requestApproval", "execCommandApproval"}:
                return {"decision": "acceptForSession"}
            if method in {"item/fileChange/requestApproval", "applyPatchApproval"}:
                return {"decision": "decline"}
            return {}

        prompt = (
            "请使用 skill://production-orchestration 编排这一条出片任务。"
            "先给出计划，再说明要加载的 Skill 和要调用的 Tool。"
            "只返回一个严格 JSON 对象，不要 Markdown。\n"
            '结构：{"skills":["production-orchestration"],'
            '"steps":[{"tool":"ingest_reference_video","arguments":{}}],'
            '"intent":{},"skeleton":{},"script_candidates":[]}.\n'
            "必须遵守开场连续块、三脚本暂停、未选定不得生成、诚实失败等不变量。\n"
            "不要再启动另一个 Agent。\n"
            f"上下文：{json.dumps(_prompt_context(context), ensure_ascii=False)}"
        )
        runtime = CodexAppServer(
            self.settings.codex_bin,
            cwd=cwd,
            timeout_seconds=max(int(self.settings.script_agent_timeout_seconds), 60),
            on_notification=on_notification,
            on_server_request=on_server_request,
            on_exit=lambda _runtime, detail: (state.update(error=f"Agent runtime exited: {detail}"), done.set()),
        )
        try:
            runtime.start().initialize()
            try:
                runtime.set_skill_roots(self.skills.runtime_roots())
            except Exception:
                logger.warning("production_agent.skill_roots_unavailable", exc_info=True)
            thread_result = runtime.start_thread(
                {
                    "cwd": cwd,
                    "sandbox": "read-only",
                    "approvalPolicy": "never",
                    "ephemeral": True,
                    "historyMode": "paginated",
                    "model": conversation.get("model"),
                    "developerInstructions": (
                        "你是任务级视频出片编排 Agent。只为当前 production 制定计划并给出 Tool 调用，"
                        "不要再拉起另一个 Agent，最终消息只能包含一个严格 JSON 对象。"
                    ),
                }
            )
            thread = thread_result.get("thread") or {}
            thread_id = str(thread.get("id") or "")
            if not thread_id:
                raise ProductionAgentError("Production Agent 未返回 thread id")
            mentions = [{"name": slug, "path": f"skill://{slug}"} for slug in VIDEO_CREATIVE_SKILLS]
            turn_result = runtime.start_turn(
                {
                    "threadId": thread_id,
                    "cwd": cwd,
                    "input": build_input(prompt, mentions),
                    "model": conversation.get("model"),
                    "effort": conversation.get("reasoning_effort"),
                    "approvalPolicy": "never",
                    "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
                    "clientUserMessageId": f"production-agent-{time.time_ns()}",
                }
            )
            turn = turn_result.get("turn") or {}
            if str(turn.get("status") or "") == "completed":
                done.set()
            deadline = time.monotonic() + max(int(self.settings.script_agent_timeout_seconds), 60)
            cancel = context.get("cancel")
            while not done.wait(0.25):
                if isinstance(cancel, threading.Event) and cancel.is_set():
                    try:
                        if turn.get("id"):
                            runtime.interrupt(thread_id, str(turn["id"]))
                    finally:
                        raise ProductionAgentError("任务已取消")
                if time.monotonic() >= deadline:
                    raise ProductionAgentError("Production Agent 规划超时")
                if not runtime.running:
                    raise ProductionAgentError(state.get("error") or "Agent 运行时已退出")
            if state.get("error"):
                raise ProductionAgentError(str(state["error"]))
            raw_text = "\n".join(
                str(text).strip() for text in state["messages"].values() if str(text).strip()
            ).strip()
            data = extract_json(raw_text)
            if not isinstance(data, dict):
                raise ProductionAgentError("Agent 未返回可解析的编排计划：" + clip_text(raw_text or "<empty>", 500))
            return data
        except CodexAppServerError as exc:
            raise ProductionAgentError(f"Production Agent 运行失败：{exc}") from exc
        finally:
            runtime.close()


class ProductionAgent:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        skills: SkillManager,
        tools: ToolRegistry,
        *,
        runtime: AgentRuntime | None = None,
        engine=None,
    ):
        self.settings = settings
        self.db = db
        self.skills = skills
        self.tools = tools
        self.engine = engine
        self.runtime = runtime if runtime is not None else RealCodexRuntime(settings, db, skills)

    def run(
        self,
        production_id: str,
        cancel: threading.Event | None = None,
        resume_from: str | None = None,
    ) -> dict[str, Any]:
        cancel = cancel or threading.Event()
        production = self.db.get_production(production_id)
        if not production:
            raise ProductionAgentError("创作任务不存在")
        if not self.runtime.is_available():
            self._fail(production_id, CODEX_UNAVAILABLE)
            raise ProductionAgentError(CODEX_UNAVAILABLE)

        context = {
            "production_id": production_id,
            "resume_from": resume_from,
            "brief": production.get("brief"),
            "status": production.get("status"),
            "selected_script_id": production.get("selected_script_id"),
            "cancel": cancel,
            "production": {
                "id": production.get("id"),
                "user_id": production.get("user_id"),
                "conversation_id": production.get("conversation_id"),
                "brief": production.get("brief"),
                "status": production.get("status"),
                "current_stage": production.get("current_stage"),
                "selected_script_id": production.get("selected_script_id"),
            },
        }
        try:
            plan = self.runtime.plan(context)
        except ProductionAgentError as exc:
            self._fail(production_id, str(exc))
            raise
        except Exception as exc:
            message = f"Production Agent 规划失败：{exc}"
            self._fail(production_id, message)
            raise ProductionAgentError(message) from exc

        skills = list(plan.get("skills") or [ORCHESTRATION_SKILL])
        steps = list(plan.get("steps") or [])
        trace = list(production.get("agent_trace") or [])
        trace.append({"type": "plan", "skills": skills, "steps": [step.get("tool") for step in steps], "resume_from": resume_from})
        self._emit(production_id, "agent_plan", {"skills": skills, "steps": [step.get("tool") for step in steps]})
        for slug in skills:
            trace.append({"type": "skill", "name": slug})
            self._emit(production_id, "skill_loaded", {"skill": slug})
        self._save_trace(production_id, trace)

        tool_context = {
            "production_id": production_id,
            "cancel": cancel,
            "conversation_id": production.get("conversation_id"),
        }
        for raw_step in steps:
            if cancel.is_set():
                self.db.update_production(production_id, {"status": "cancelled"})
                self._emit(production_id, "error", {"stage": "cancelled", "message": "已取消"})
                return self.db.get_production(production_id) or production
            name = str(raw_step.get("tool") or raw_step.get("name") or "").strip()
            arguments = dict(raw_step.get("arguments") or {})
            arguments.setdefault("production_id", production_id)
            if name == "write_intent_and_scripts":
                if plan.get("intent") and "intent" not in arguments:
                    arguments["intent"] = plan["intent"]
                if plan.get("skeleton") and "skeleton" not in arguments:
                    arguments["skeleton"] = plan["skeleton"]
                if plan.get("script_candidates") and "script_candidates" not in arguments:
                    arguments["script_candidates"] = plan["script_candidates"]
            if name in {"generate_video_shots", "compose_timeline", "burn_subtitles"}:
                current = self.db.get_production(production_id) or {}
                if not current.get("selected_script_id"):
                    message = "还没有选定脚本，不能生成视频"
                    self._fail(production_id, message)
                    raise ProductionAgentError(message)
            if name not in CAPABILITY_TOOLS and name not in {tool.name for tool in self.tools.all()}:
                message = f"未知工具：{name}"
                self._fail(production_id, message)
                raise ProductionAgentError(message)
            self._emit(production_id, "tool_started", {"tool": name, "arguments": {k: v for k, v in arguments.items() if k != "script_candidates"}})
            result = self.tools.execute(name, arguments, tool_context)
            text = _tool_text(result)
            if contains_forbidden_claim(text):
                message = "工具结果含有禁止的还原表述，任务已失败"
                self._fail(production_id, message)
                raise ProductionAgentError(message)
            success = bool(result.get("success"))
            self._emit(production_id, "tool_completed", {"tool": name, "success": success, "text": clip_text(text, 800)})
            trace.append({"type": "tool", "name": name, "success": success})
            self._save_trace(production_id, trace)
            if not success:
                message = text or f"工具 {name} 执行失败"
                self._fail(production_id, message)
                raise ProductionAgentError(message)
            current = self.db.get_production(production_id) or {}
            if current.get("status") == "awaiting_script_choice":
                self._emit(production_id, "awaiting_script_choice", {"message": "请选择创意脚本"})
                return current
            if current.get("status") in {"failed", "cancelled"}:
                return current

        current = self.db.get_production(production_id)
        if not current:
            raise ProductionAgentError("创作任务在完成后丢失")
        if current.get("status") == "running":
            # A plan that ends without compose should not pretend the film is done.
            if (current.get("output") or {}).get("video_path"):
                self.db.update_production(production_id, {"status": "completed", "current_stage": "done", "error": None})
                self._emit(production_id, "completed", {"stage": "done"})
            else:
                self.db.update_production(production_id, {"status": "failed", "error": "编排计划已结束但还没有成片"})
                self._emit(production_id, "error", {"message": "编排计划已结束但还没有成片"})
        elif current.get("status") == "completed":
            self._emit(production_id, "completed", {"stage": "done"})
        return self.db.get_production(production_id) or current

    def _save_trace(self, production_id: str, trace: list[dict[str, Any]]) -> None:
        self.db.update_production(production_id, {"agent_trace": trace})

    def _fail(self, production_id: str, message: str) -> None:
        clean = message
        for phrase in FORBIDDEN_CLAIMS:
            clean = clean.replace(phrase, "未完成还原")
        self.db.update_production(production_id, {"status": "failed", "error": clean})
        self._emit(production_id, "error", {"message": clean})

    def _emit(self, production_id: str, kind: str, payload: dict[str, Any]) -> None:
        if self.engine is not None:
            self.engine._emit(production_id, kind, payload)
            return
        self.db.append_production_event(production_id, kind, payload)


def _tool_text(result: dict[str, Any]) -> str:
    items = result.get("contentItems") or []
    parts = [str(item.get("text") or "") for item in items if isinstance(item, dict)]
    return "\n".join(part for part in parts if part)
