"""Deterministic production runner: plan locally, call tools, pause at checkpoints."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Protocol

from .config import Settings
from .db import Database
from .jobs import revision
from .logging_utils import clip_text
from .production_tools import (
    CAPABILITY_TOOLS,
    FORBIDDEN_CLAIMS,
    contains_forbidden_claim,
)
from .skills import SkillManager
from .tools import ToolRegistry

ORCHESTRATION_SKILL = "production-orchestration"
VIDEO_CREATIVE_SKILLS = (
    "production-orchestration",
    "video-references",
    "creative-intent-script-skeleton",
    "universal-ugc-video-script-generation",
)
CODEX_UNAVAILABLE = "Codex 不可用，无法生成新的创意脚本。"


class ProductionAgentError(RuntimeError):
    pass


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


class DeterministicPlanner:
    """Build the production plan locally. Creative content comes from the conversation turn."""

    def is_available(self) -> bool:
        return True

    def plan(self, context: dict[str, Any]) -> dict[str, Any]:
        production = context.get("production") or {}
        resume_from = str(context.get("resume_from") or "ingest")
        if context.get("prepare_only"):
            tools = [
                "ingest_reference_video",
                "understand_reference_video",
                "extract_landing_points",
                "search_video_references",
            ]
        elif resume_from == "generate":
            tools = ["generate_candidate_videos"]
        elif resume_from == "finalize":
            tools = ["compose_timeline", "burn_subtitles"]
        elif resume_from == "decompose":
            tools = [
                "understand_reference_video",
                "extract_landing_points",
                "search_video_references",
                "lock_timeline_opening",
                "write_intent_and_scripts",
            ]
        elif resume_from == "intent":
            tools = [
                "extract_landing_points",
                "search_video_references",
                "lock_timeline_opening",
                "write_intent_and_scripts",
            ]
        elif resume_from == "script":
            tools = [
                "lock_timeline_opening",
                "write_intent_and_scripts",
            ]
        else:
            tools = [
                "ingest_reference_video",
                "understand_reference_video",
                "extract_landing_points",
                "search_video_references",
                "lock_timeline_opening",
                "write_intent_and_scripts",
            ]
        return {
            "skills": list(VIDEO_CREATIVE_SKILLS),
            "steps": [{"tool": name, "arguments": {}} for name in tools],
            "intent": production.get("intent"),
            "skeleton": production.get("skeleton"),
            "script_candidates": production.get("script_candidates") or [],
        }


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
        self.runtime = runtime if runtime is not None else DeterministicPlanner()

    def run(
        self,
        production_id: str,
        cancel: threading.Event | None = None,
        resume_from: str | None = None,
        prepare_only: bool = False,
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
            "prepare_only": prepare_only,
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
                "intent": production.get("intent"),
                "skeleton": production.get("skeleton"),
                "script_candidates": production.get("script_candidates") or [],
                "candidate_videos": production.get("candidate_videos") or [],
            },
        }
        recovered_steps: list[str] = []
        try:
            if resume_from == "recover":
                trace = list(production.get("agent_trace") or [])
                index = next((i for i in range(len(trace) - 1, -1, -1) if trace[i].get("type") == "plan"), None)
                if index is None:
                    if not production.get("selected_script_id"):
                        raise ProductionAgentError("中断任务缺少可恢复计划")
                    plan = DeterministicPlanner().plan({**context, "resume_from": "finalize"})
                else:
                    saved = trace[index]
                    plan = {"steps": [{"tool": name} for name in saved["steps"]], "skills": saved.get("skills")}
                    prepare_only = bool(saved.get("prepare_only"))
                    recovered_steps = list(saved.get("completed_steps") or [])
                    for entry in trace[index + 1:]:
                        if entry.get("type") == "tool" and entry.get("success"):
                            recovered_steps.append(entry["name"])
                    names = saved["steps"]
                    if recovered_steps != names[:len(recovered_steps)]:
                        raise ProductionAgentError("中断计划的成功步骤不是连续前缀，无法安全恢复")
            else:
                plan = self.runtime.plan(context)
        except ProductionAgentError as exc:
            self._fail(production_id, str(exc))
            raise
        except Exception as exc:
            message = f"创作编排计划生成失败：{exc}"
            self._fail(production_id, message)
            raise ProductionAgentError(message) from exc

        skills = list(plan.get("skills") or [ORCHESTRATION_SKILL])
        steps = list(plan.get("steps") or [])
        trace = list(production.get("agent_trace") or [])
        needs_scripts = any(
            str(step.get("tool") or step.get("name") or "") == "write_intent_and_scripts"
            for step in steps[len(recovered_steps):]
        )
        candidates = plan.get("script_candidates") or production.get("script_candidates") or []
        if needs_scripts and len(candidates) != 3:
            message = "缺少创意内容：请先在当前 Codex 对话中生成 intent、skeleton 和 3 个脚本候选。"
            self._fail(production_id, message)
            raise ProductionAgentError(message)
        trace.append(
            {
                "type": "plan",
                "skills": skills,
                "steps": [step.get("tool") for step in steps],
                "resume_from": resume_from,
                "prepare_only": prepare_only,
                "completed_steps": recovered_steps.copy(),
            }
        )
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
        completed_steps = recovered_steps.copy()
        for raw_step in steps[len(recovered_steps):]:
            if cancel.is_set():
                self.db.cancel_production_jobs(production_id)
                self.db.update_production(production_id, {"status": "cancelled"})
                self._emit(production_id, "error", {"stage": "cancelled", "message": "已取消"})
                return self.db.get_production(production_id) or production
            name = str(raw_step.get("tool") or raw_step.get("name") or "").strip()
            arguments = dict(raw_step.get("arguments") or {})
            arguments.setdefault("production_id", production_id)
            if name == "write_intent_and_scripts":
                current = self.db.get_production(production_id) or {}
                intent = current.get("intent") or plan.get("intent")
                skeleton = current.get("skeleton") or plan.get("skeleton")
                candidates = plan.get("script_candidates") or current.get("script_candidates")
                if intent and "intent" not in arguments:
                    arguments["intent"] = intent
                if skeleton and "skeleton" not in arguments:
                    arguments["skeleton"] = skeleton
                if candidates and "script_candidates" not in arguments:
                    arguments["script_candidates"] = candidates
            if name in {"compose_timeline", "burn_subtitles"}:
                current = self.db.get_production(production_id) or {}
                if not current.get("selected_script_id"):
                    message = "还没有选定视频，不能继续最终合成"
                    self._fail(production_id, message)
                    raise ProductionAgentError(message)
            if name not in CAPABILITY_TOOLS and name not in {tool.name for tool in self.tools.all()}:
                message = f"未知工具：{name}"
                self._fail(production_id, message)
                raise ProductionAgentError(message)
            tool_arguments = {key: value for key, value in arguments.items() if key != "script_candidates"}
            self._emit(production_id, "tool_started", {"tool": name, "arguments": tool_arguments})
            result = self.tools.execute(name, arguments, tool_context)
            text = _tool_text(result)
            if contains_forbidden_claim(text):
                message = "工具结果含有禁止的还原表述，任务已失败"
                self._fail(production_id, message)
                raise ProductionAgentError(message)
            success = bool(result.get("success"))
            self._emit(
                production_id,
                "tool_completed",
                {"tool": name, "success": success, "text": clip_text(text, 800)},
            )
            trace.append({"type": "tool", "name": name, "success": success})
            self._save_trace(production_id, trace)
            if cancel.is_set() or (self.db.get_production(production_id) or {}).get("status") == "cancelled":
                self.db.cancel_production_jobs(production_id)
                self.db.update_production(production_id, {"status": "cancelled"})
                return self.db.get_production(production_id)
            if not success:
                message = text or f"工具 {name} 执行失败"
                self._fail(production_id, message)
                raise ProductionAgentError(message)
            completed_steps.append(name)
            current = self.db.get_production(production_id) or {}
            if current.get("status") == "awaiting_script_approval":
                self._emit(
                    production_id,
                    "awaiting_script_approval",
                    {"message": "创意脚本已生成，等待用户确认后再生视频"},
                )
                return current
            if current.get("status") == "awaiting_video_choice":
                self._emit(production_id, "awaiting_video_choice", {"message": "候选视频已生成"})
                return current
            if current.get("status") in {"failed", "cancelled"}:
                return current

        current = self.db.get_production(production_id)
        if not current:
            raise ProductionAgentError("创作任务在完成后丢失")
        if prepare_only:
            self.db.update_production(
                production_id,
                {
                    "status": "awaiting_creative_context",
                    "current_stage": "intent",
                    "error": None,
                },
            )
            self._emit(
                production_id,
                "awaiting_creative_context",
                {"message": "参考视频拆解与脚本检索已完成，等待创意内容"},
            )
            return self.db.get_production(production_id) or current
        self._finalize(production_id, steps, completed_steps, cancel)
        return self.db.get_production(production_id) or current

    def _finalize(self, production_id, steps, completed_steps, cancel):
        current = self.db.get_production(production_id) or {}
        if cancel.is_set() or current.get("status") == "cancelled":
            self.db.cancel_production_jobs(production_id)
            self.db.update_production(production_id, {"status": "cancelled"})
            return
        expected = [str(step.get("tool") or step.get("name") or "") for step in steps]
        if not any(name in expected for name in ("compose_timeline", "burn_subtitles")):
            return
        output = current.get("output") or {}
        path = Path(output.get("video_path") or "")
        jobs = [job for job in self.db.list_production_jobs(production_id)
                if job["input_json"].get("revision") == revision(current)]
        error = None
        if completed_steps != expected:
            error = "编排计划必做步骤未全部成功，不能使用旧成片"
        elif any(job["status"] != "succeeded" for job in jobs):
            if any(job["status"] == "cancelled" for job in jobs):
                self.db.update_production(production_id, {"status": "cancelled"})
                return
            error = "编排计划仍有未完成或失败的相关作业"
        elif not path.is_file() or path.stat().st_size <= 0:
            error = "编排计划已结束但最终成片不存在或为空"
        elif "burn_subtitles" in expected and not any(
            job["tool"] == "burn_subtitles" and job["status"] == "succeeded"
            and job["result_json"].get("video_path") == str(path)
            and output.get("subtitled_video_path") == str(path) for job in jobs
        ):
            error = "计划要求烧字幕，但最终成片不是已成功烧录的产物"
        elif not any(job["result_json"].get("video_path") == str(path) for job in jobs):
            error = "最终成片未关联本次计划的成功作业，不能使用旧成片"
        if error:
            self._fail(production_id, error)
            return
        with self.db.transaction() as connection:
            updated = connection.execute(
                "UPDATE productions SET status='completed',current_stage='done',error=NULL "
                "WHERE id=? AND status='running'",
                (production_id,),
            ).rowcount
        if updated:
            self._emit(production_id, "completed", {"stage": "done"})

    def _save_trace(self, production_id: str, trace: list[dict[str, Any]]) -> None:
        self.db.update_production(production_id, {"agent_trace": trace})

    def _fail(self, production_id: str, message: str) -> None:
        clean = message
        for phrase in FORBIDDEN_CLAIMS:
            clean = clean.replace(phrase, "未完成还原")
        with self.db.transaction() as connection:
            updated = connection.execute(
                "UPDATE productions SET status='failed',error=? WHERE id=? AND status!='cancelled'",
                (clean, production_id),
            ).rowcount
        if updated:
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
