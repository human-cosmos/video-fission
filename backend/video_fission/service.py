from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .app_server import CodexAppServer, CodexAppServerError
from .config import Settings
from .db import Database, utc_now
from .protocol import (
    APPROVAL_METHODS,
    accumulate_payload,
    approval_response,
    build_input,
    classify_approval,
    event_kind,
    extract_item_id,
    flatten_text,
    item_type_for_method,
    normalize_item_type,
    question_list,
)
from .skills import SkillManager
from .tools import ToolRegistry

logger = logging.getLogger(__name__)
RUNTIME_IDLE_SECONDS = 30 * 60


class ConversationNotFound(LookupError):
    pass


class ConversationBusy(RuntimeError):
    pass


class RuntimeUnavailable(RuntimeError):
    pass


class CodexHub:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        skills: SkillManager,
        tools: ToolRegistry,
    ):
        self.settings = settings
        self.db = db
        self.skills = skills
        self.tools = tools
        self._runtimes: dict[str, CodexAppServer] = {}
        self._runtime_creation_locks: dict[str, threading.Lock] = {}
        self._turn_locks: dict[str, threading.Lock] = {}
        self._lock = threading.RLock()
        self._approval_waiters: dict[str, dict[str, Any]] = {}
        self._clarification_waiters: dict[str, dict[str, Any]] = {}
        self._models_cache: tuple[float, list[dict[str, Any]]] | None = None
        self._runtime_probe_cache: tuple[float, dict[str, Any]] | None = None

    def initialize(self) -> None:
        self.settings.ensure_directories()
        self.db.initialize()
        self._cleanup_orphan_uploads()
        self._cleanup_orphan_artifacts()
        recovery_message = "服务重启，上一轮执行已中断"
        for conversation_id in self.db.recover_orphaned_runs(recovery_message):
            self.db.append_event(
                conversation_id,
                "runtime.recovered",
                {"message": recovery_message},
            )

    def _cleanup_orphan_uploads(self, max_age_seconds: int = 86_400) -> None:
        cutoff = time.time() - max_age_seconds
        for path in self.settings.uploads_dir.iterdir():
            if not path.is_file() or path.name.startswith("."):
                continue
            try:
                if path.stat().st_mtime < cutoff and self.db.attachment_reference_count(str(path.resolve())) == 0:
                    path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Failed to clean orphan upload: %s", path, exc_info=True)

    def _cleanup_orphan_artifacts(self, max_age_seconds: int = 86_400) -> None:
        cutoff = time.time() - max_age_seconds
        for path in self.settings.artifacts_dir.iterdir():
            if path.name == "productions":
                continue
            if not path.is_dir() or path.is_symlink() or self.db.get_conversation(path.name):
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    shutil.rmtree(path)
            except OSError:
                logger.warning("Failed to clean orphan artifact directory: %s", path, exc_info=True)

    def shutdown(self) -> None:
        for conversation_id in list(self._turn_locks):
            self._cancel_interactions(conversation_id)
        with self._lock:
            runtimes = list(self._runtimes.values())
            self._runtimes.clear()
        for runtime in runtimes:
            try:
                runtime.close()
            except Exception:
                logger.debug("Failed to close Codex runtime", exc_info=True)

    def runtime_status(self) -> dict[str, Any]:
        bin_path = self.settings.codex_bin
        if self._runtime_probe_cache and time.time() - self._runtime_probe_cache[0] < 15:
            probe = self._runtime_probe_cache[1]
            return {
                **probe,
                "online_conversations": len(self._runtimes),
            }
        version = None
        available = False
        warning = None
        if bin_path and Path(bin_path).exists():
            try:
                result = subprocess.run(
                    [bin_path, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=8,
                    check=False,
                )
                version = (result.stdout or result.stderr).strip() or None
                available = result.returncode == 0
                if not available:
                    warning = "Codex CLI 无法执行"
            except (OSError, subprocess.SubprocessError) as exc:
                warning = str(exc)
        else:
            warning = "智能中枢运行时不可用。"
        probe = {
            "available": available,
            "codex_bin": bin_path,
            "version": version,
            "runtime_command": f"{bin_path or 'codex'} app-server --stdio",
            "workspace": str(self.settings.workspace),
            "warning": warning,
        }
        self._runtime_probe_cache = (time.time(), probe)
        return {**probe, "online_conversations": len(self._runtimes)}

    def capabilities(self) -> dict[str, Any]:
        return {
            "runtime": self.runtime_status(),
            "skills": self.skills.list(),
            "tools": self.tools.summary(),
        }

    def list_conversations(self, include_archived: bool = False) -> list[dict[str, Any]]:
        self._close_idle_runtimes()
        return [self._public_conversation(row) for row in self.db.list_conversations(include_archived)]

    def _conversation(self, conversation_id: str) -> dict[str, Any]:
        row = self.db.get_conversation(conversation_id)
        if not row:
            raise ConversationNotFound("对话不存在")
        return row

    @staticmethod
    def _public_conversation(row: dict[str, Any]) -> dict[str, Any]:
        return {
            **row,
            "is_running": row.get("status") == "running",
            "is_archived": bool(row.get("archived_at")),
        }

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        self._close_idle_runtimes()
        row = self._conversation(conversation_id)
        latest_event_id = self.db.latest_event_id(conversation_id)
        return {
            **self._public_conversation(row),
            "items": self.db.list_items(conversation_id),
            "pending_approvals": self.db.pending_approvals(conversation_id),
            "pending_clarifications": self.db.pending_clarifications(conversation_id),
            "tool_runs": self.db.list_tool_runs(conversation_id),
            "latest_event_id": latest_event_id,
        }

    def create_conversation(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        row = self.db.create_conversation(
            {
                "title": payload.get("title") or "新对话",
                "cwd": str(self.settings.workspace),
                "model": payload.get("model"),
                "reasoning_effort": payload.get("reasoning_effort"),
                "developer_instructions": payload.get("developer_instructions") or "",
            }
        )
        return self._public_conversation(row)

    def update_conversation(self, conversation_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        row = self._conversation(conversation_id)
        updates: dict[str, Any] = {}
        if "title" in payload:
            title = str(payload.get("title") or "").strip()
            if title:
                updates["title"] = title
        if "model" in payload:
            updates["model"] = str(payload.get("model") or "").strip() or None
        if "reasoning_effort" in payload:
            updates["reasoning_effort"] = str(payload.get("reasoning_effort") or "").strip() or None
        if "developer_instructions" in payload:
            updates["developer_instructions"] = str(payload.get("developer_instructions") or "")
        if updates:
            self.db.update_conversation(conversation_id, updates)
            row.update(updates)
            if updates.get("title") and row.get("codex_thread_id"):
                runtime = self._runtimes.get(conversation_id)
                if runtime and runtime.running:
                    try:
                        runtime.set_thread_name(row["codex_thread_id"], updates["title"])
                    except Exception:
                        logger.warning("Failed to sync Codex thread title", exc_info=True)
        return self._public_conversation(row)

    def _conversation_lock(self, conversation_id: str) -> threading.Lock:
        with self._lock:
            return self._turn_locks.setdefault(conversation_id, threading.Lock())

    def send_message(self, conversation_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        row = self._conversation(conversation_id)
        if row.get("archived_at"):
            raise ConversationBusy("已归档会话不能继续发送消息")
        text = str(payload.get("text") or "").strip()
        files = payload.get("files") or []
        if not text and not files:
            raise ValueError("消息不能为空")
        if row.get("status") == "running":
            raise ConversationBusy("当前对话已有进行中的一轮")
        if not self.settings.codex_bin:
            raise RuntimeUnavailable("智能中枢运行时不可用")
        lock = self._conversation_lock(conversation_id)
        if not lock.acquire(blocking=False):
            raise ConversationBusy("当前对话已有进行中的一轮")
        try:
            row = self._conversation(conversation_id)
            if row.get("archived_at"):
                raise ConversationBusy("已归档会话不能继续发送消息")
            if row.get("status") == "running":
                raise ConversationBusy("当前对话已有进行中的一轮")
            files = self._validated_files(files)
            generation = int(row.get("turn_generation") or 0) + 1
            model_text = self._text_with_files(text, files)
            mentions = payload.get("mentions") or []
            item = self.db.add_item(
                conversation_id,
                {
                    "item_type": "userMessage",
                    "role": "user",
                    "status": "completed",
                    "payload": {
                        "text": text,
                        "mentions": mentions,
                        "files": files,
                    },
                },
            )
            self.db.append_event(conversation_id, "item.local", item)
            updates: dict[str, Any] = {
                "status": "running",
                "turn_generation": generation,
                "last_error": None,
            }
            if row.get("title") in {None, "", "新对话"}:
                updates["title"] = (text or Path(str(files[0].get("name", "附件"))).stem)[:36]
            self.db.update_conversation(conversation_id, updates)
            thread = threading.Thread(
                target=self._run_turn,
                args=(conversation_id, model_text, mentions, generation, lock),
                daemon=True,
                name=f"video-fission-turn-{conversation_id[:8]}",
            )
            thread.start()
            return {
                "conversation_id": conversation_id,
                "status": "running",
                "turn_generation": generation,
            }
        except Exception:
            if lock.locked():
                lock.release()
            raise

    def steer(self, conversation_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        row = self._conversation(conversation_id)
        runtime = self._runtimes.get(conversation_id)
        if (
            row.get("status") != "running"
            or not runtime
            or not row.get("codex_thread_id")
            or not row.get("active_turn_id")
        ):
            return self.send_message(conversation_id, payload)
        text = str(payload.get("text") or "").strip()
        files = payload.get("files") or []
        if not text and not files:
            raise ValueError("追问内容不能为空")
        files = self._validated_files(files)
        model_text = self._text_with_files(text, files)
        mentions = payload.get("mentions") or []
        item = self.db.add_item(
            conversation_id,
            {
                "item_type": "userMessage",
                "role": "user",
                "status": "completed",
                "payload": {"text": text, "mentions": mentions, "files": files, "steer": True},
            },
        )
        self.db.append_event(conversation_id, "item.local", item)
        runtime.steer_turn(
            row["codex_thread_id"],
            row["active_turn_id"],
            build_input(model_text, mentions),
        )
        self.db.append_event(
            conversation_id,
            "turn.steered",
            {"text": text, "turn_generation": row.get("turn_generation")},
        )
        return {"status": "running", "steered": True}

    def interrupt(self, conversation_id: str) -> dict[str, Any]:
        row = self._conversation(conversation_id)
        runtime = self._runtimes.get(conversation_id)
        if runtime and row.get("codex_thread_id") and row.get("active_turn_id"):
            try:
                runtime.interrupt(row["codex_thread_id"], row["active_turn_id"])
            except Exception:
                logger.warning("Interrupt RPC failed", exc_info=True)
        self._cancel_interactions(conversation_id)
        generation = int(row.get("turn_generation") or 0) + 1
        self.db.update_conversation(
            conversation_id,
            {
                "status": "idle",
                "active_turn_id": None,
                "turn_generation": generation,
            },
        )
        self.db.append_event(
            conversation_id,
            "turn.interrupted",
            {"status": "idle", "turn_generation": generation},
        )
        return {"status": "interrupted", "turn_generation": generation}

    def compact(self, conversation_id: str) -> dict[str, Any]:
        row = self._conversation(conversation_id)
        runtime = self._ensure_runtime(row)
        if not row.get("codex_thread_id"):
            raise ValueError("会话尚未创建 Codex Thread")
        return {"result": runtime.compact(row["codex_thread_id"])}

    def archive(self, conversation_id: str, archived: bool) -> dict[str, Any]:
        row = self._conversation(conversation_id)
        if archived and row.get("status") == "running":
            raise ConversationBusy("对话正在执行，暂时不能归档")
        runtime = self._runtimes.get(conversation_id)
        if runtime and row.get("codex_thread_id"):
            try:
                runtime.archive_thread(row["codex_thread_id"], archived)
            except Exception:
                logger.warning("Codex archive RPC failed", exc_info=True)
        self.db.update_conversation(
            conversation_id,
            {
                "archived_at": utc_now() if archived else None,
                "status": "archived" if archived else "idle",
            },
        )
        if archived:
            closed_runtime = self._runtimes.pop(conversation_id, None)
            if closed_runtime:
                closed_runtime.close()
        self.db.append_event(
            conversation_id,
            "conversation.archived" if archived else "conversation.unarchived",
            {},
        )
        return self._public_conversation(self._conversation(conversation_id))

    def _close_idle_runtimes(self) -> None:
        now = time.time()
        with self._lock:
            candidates = list(self._runtimes.items())
        for conversation_id, runtime in candidates:
            row = self.db.get_conversation(conversation_id)
            if not row or row.get("status") == "running":
                continue
            if runtime.last_activity and now - runtime.last_activity < RUNTIME_IDLE_SECONDS:
                continue
            with self._lock:
                if self._runtimes.get(conversation_id) is not runtime:
                    continue
                self._runtimes.pop(conversation_id, None)
            runtime.close()

    def delete(self, conversation_id: str) -> None:
        row = self._conversation(conversation_id)
        if row.get("status") == "running":
            self.interrupt(conversation_id)
        attachment_paths = {
            str(file.get("path") or "")
            for item in self.db.list_items(conversation_id)
            for file in (item.get("payload") or {}).get("files") or []
            if isinstance(file, dict)
        }
        attachment_paths.discard("")
        runtime = self._runtimes.pop(conversation_id, None)
        if runtime:
            runtime.close()
        self.db.delete_conversation(conversation_id)
        upload_root = self.settings.uploads_dir.resolve()
        for raw_path in attachment_paths:
            path = Path(raw_path).resolve()
            if upload_root in path.parents and self.db.attachment_reference_count(str(path)) == 0:
                path.unlink(missing_ok=True)
        artifact_dir = self.settings.artifacts_dir / conversation_id
        if artifact_dir.is_dir():
            shutil.rmtree(artifact_dir)

    def fork(self, conversation_id: str) -> dict[str, Any]:
        source = self._conversation(conversation_id)
        if not source.get("codex_thread_id"):
            raise ValueError("会话尚未建立 Codex Thread")
        runtime = self._runtimes.get(conversation_id)
        temporary = False
        if not runtime or not runtime.running:
            runtime = self._new_runtime(
                cwd=str(self.settings.workspace),
                on_notification=None,
                on_server_request=None,
            )
            temporary = True
        try:
            result = runtime.fork_thread(source["codex_thread_id"])
        finally:
            if temporary:
                runtime.close()
        thread = (result or {}).get("thread") or {}
        thread_id = thread.get("id")
        target = self.db.create_conversation(
            {
                "title": f"{source['title']} · 分叉",
                "cwd": str(self.settings.workspace),
                "model": source.get("model"),
                "reasoning_effort": source.get("reasoning_effort"),
                "developer_instructions": source.get("developer_instructions") or "",
            }
        )
        self.db.update_conversation(target["id"], {"codex_thread_id": thread_id})
        self.db.copy_items(conversation_id, target["id"])
        self.db.append_event(target["id"], "conversation.forked", {"source_id": conversation_id})
        return self._public_conversation(self._conversation(target["id"]))

    def sync(self, conversation_id: str) -> dict[str, Any]:
        row = self._conversation(conversation_id)
        if not row.get("codex_thread_id"):
            raise ValueError("会话尚未建立 Codex Thread")
        runtime = self._ensure_runtime(row)
        entries = runtime.list_all_items(row["codex_thread_id"])
        synced = 0
        for entry in entries:
            item = entry.get("item") if isinstance(entry.get("item"), dict) else entry
            item_id = str(item.get("id") or entry.get("id") or "")
            item_type = normalize_item_type(item)
            if not item_id or not item_type or item_type == "userMessage":
                continue
            self.db.upsert_codex_item(
                conversation_id,
                item_id,
                {
                    "item_type": item_type,
                    "role": "assistant" if item_type == "agentMessage" else None,
                    "status": "completed",
                    "payload": item,
                },
            )
            synced += 1
        self.db.append_event(conversation_id, "conversation.synced", {"items": synced})
        return {"synced": synced}

    def list_models(self, refresh: bool = False) -> list[dict[str, Any]]:
        if not refresh and self._models_cache and time.time() - self._models_cache[0] < 60:
            return self._models_cache[1]
        if not self.settings.codex_bin:
            return []
        runtime = self._new_runtime(
            cwd=str(self.settings.workspace),
            on_notification=None,
            on_server_request=None,
        )
        try:
            payload = runtime.list_models()
        finally:
            runtime.close()
        rows = payload.get("data") if isinstance(payload, dict) else []
        models = [
            {
                "id": row.get("id") or row.get("model"),
                "name": row.get("displayName") or row.get("model") or row.get("id"),
                "description": row.get("description") or "",
                "default_reasoning_effort": row.get("defaultReasoningEffort") or "medium",
                "supported_reasoning_efforts": [
                    item.get("reasoningEffort")
                    for item in row.get("supportedReasoningEfforts") or []
                    if isinstance(item, dict) and item.get("reasoningEffort")
                ],
                "input_modalities": row.get("inputModalities") or ["text"],
                "is_default": bool(row.get("isDefault")),
            }
            for row in rows
            if isinstance(row, dict) and row.get("hidden") is not True
        ]
        self._models_cache = (time.time(), models)
        return models

    def runtime_skills(self) -> dict[str, Any]:
        runtime = self._new_runtime(
            cwd=str(self.settings.workspace),
            on_notification=None,
            on_server_request=None,
        )
        try:
            payload = runtime.list_skills([str(self.settings.workspace)], refresh=True)
        finally:
            runtime.close()
        shards = payload.get("data") if isinstance(payload, dict) else []
        items: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for shard in shards or []:
            if not isinstance(shard, dict):
                continue
            if shard.get("name"):
                items.append(shard)
            else:
                items.extend(item for item in shard.get("skills") or [] if isinstance(item, dict))
            errors.extend(item for item in shard.get("errors") or [] if isinstance(item, dict))
        errors.extend(item for item in payload.get("errors") or [] if isinstance(item, dict))
        return {
            "roots": self.skills.runtime_roots,
            "items": items,
            "errors": errors,
        }

    def decide_approval(self, approval_id: str, decision: str) -> dict[str, Any]:
        if decision not in {"accept", "acceptForSession", "decline", "cancel"}:
            raise ValueError("无效的审批决定")
        record = self.db.get_approval(approval_id)
        if not record:
            raise ConversationNotFound("审批请求不存在")
        if record.get("decision"):
            return {"approval_id": approval_id, "decision": record["decision"]}
        waiter = self._approval_waiters.get(approval_id)
        if not waiter:
            raise ConversationBusy("审批请求已过期")
        self.db.resolve_approval(approval_id, decision)
        waiter["decision"] = decision
        waiter["event"].set()
        self.db.append_event(
            record["conversation_id"],
            "approval.resolved",
            {"approval_id": approval_id, "decision": decision},
        )
        return {"approval_id": approval_id, "decision": decision}

    def answer_clarification(self, clarification_id: str, answers: dict[str, Any]) -> dict[str, Any]:
        record = self.db.get_clarification(clarification_id)
        if not record:
            raise ConversationNotFound("澄清请求不存在")
        if record.get("answers") is not None:
            return {"clarification_id": clarification_id, "answers": record["answers"]}
        waiter = self._clarification_waiters.get(clarification_id)
        if not waiter:
            raise ConversationBusy("澄清请求已过期")
        self.db.resolve_clarification(clarification_id, answers)
        waiter["answers"] = answers
        waiter["event"].set()
        self.db.append_event(
            record["conversation_id"],
            "clarification.resolved",
            {"clarification_id": clarification_id},
        )
        return {"clarification_id": clarification_id, "answers": answers}

    def _text_with_files(self, text: str, files: list[dict[str, Any]]) -> str:
        if not files:
            return text
        lines = [text] if text else []
        lines.append("[用户上传的文件。请只在需要时读取，所有路径均为只读附件]")
        for index, file in enumerate(files, start=1):
            name = str(file.get("name") or f"附件 {index}")
            path = str(file.get("path") or "")
            if path:
                lines.append(f"{index}. {name} — `{path}`")
        return "\n\n".join(lines)

    def _validated_files(self, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
        root = self.settings.uploads_dir.resolve()
        validated: list[dict[str, Any]] = []
        for item in files:
            if not isinstance(item, dict):
                raise ValueError("附件格式无效")
            raw_path = str(item.get("path") or "").strip()
            if not raw_path:
                raise ValueError("附件路径不能为空")
            path = Path(raw_path).expanduser().resolve()
            if root not in path.parents or not path.is_file():
                raise ValueError("附件不存在或不属于当前应用")
            validated.append(
                {
                    **item,
                    "id": str(item.get("id") or path.stem),
                    "name": Path(str(item.get("name") or path.name)).name,
                    "path": str(path),
                }
            )
        return validated

    def _system_instructions(self, row: dict[str, Any]) -> str:
        user_instructions = str(row.get("developer_instructions") or "").strip()
        tool_names = ", ".join(tool.name for tool in self.tools.all())
        return f"""你是 Video Fission 的智能中枢，一名擅长视频策划、内容拆解、脚本和制作组织的工作 Agent。

工作原则：
- 默认在只读隔离工作区中工作，不声称执行了没有真实发生的操作。
- 先理解用户目标，再给出具体、可执行的结论；需要澄清时只问真正影响结果的问题。
- 你拥有动态工具：{tool_names}。当用户要求统计、查看工作区、读取文件或生成并保存视频简报时，应调用对应工具。
- 项目 Skill 位于已注册的技能根目录，可通过 skill:// 提及或在任务匹配时使用。
- 当任务涉及参考视频复刻、片段重组或生成 UGC 脚本时，骨架尚未明确时先用 creative-intent-script-skeleton 收敛意图和骨架；用户已确认骨架时直接使用 universal-ugc-video-script-generation 生成完整分镜。两个阶段都按需使用 video-references，不得声称检索过实际未读取的参考脚本。
- 文件附件可能是绝对路径；读取前先判断是否真的需要。
- 用中文回答，代码、命令、路径和标识保持原样。

{user_instructions}
""".strip()

    def _new_runtime(
        self,
        *,
        cwd: str,
        on_notification: Any,
        on_server_request: Any,
        on_exit: Any = None,
    ) -> CodexAppServer:
        if not self.settings.codex_bin:
            raise RuntimeUnavailable("智能中枢运行时不可用")
        runtime = CodexAppServer(
            self.settings.codex_bin,
            cwd=cwd,
            timeout_seconds=self.settings.codex_timeout_seconds,
            on_notification=on_notification,
            on_server_request=on_server_request,
            on_exit=on_exit,
        ).start()
        runtime.initialize()
        try:
            runtime.set_skill_roots(self.skills.runtime_roots)
        except Exception:
            logger.warning("Codex Skills API unavailable; continuing without extra roots", exc_info=True)
        return runtime

    def _ensure_runtime(self, row: dict[str, Any]) -> CodexAppServer:
        conversation_id = row["id"]
        with self._lock:
            creation_lock = self._runtime_creation_locks.setdefault(conversation_id, threading.Lock())
        with creation_lock:
            runtime = self._runtimes.get(conversation_id)
            if runtime and runtime.running:
                return runtime
            if runtime:
                runtime.close()
            runtime = self._new_runtime(
                cwd=row.get("cwd") or str(self.settings.workspace),
                on_notification=lambda method, params: self._on_notification(conversation_id, method, params),
                on_server_request=lambda message: self._on_server_request(conversation_id, message),
                on_exit=lambda failed_runtime, detail: self._on_runtime_exit(conversation_id, failed_runtime, detail),
            )
            payload = {
                "cwd": row.get("cwd") or str(self.settings.workspace),
                "sandbox": "read-only",
                "approvalPolicy": "on-request",
                "approvalsReviewer": "user",
                "ephemeral": False,
                "historyMode": "paginated",
                "model": row.get("model"),
                "developerInstructions": self._system_instructions(row),
                "dynamicTools": self.tools.specs(),
            }
            try:
                if row.get("codex_thread_id"):
                    overrides = {
                        key: value for key, value in payload.items() if key not in {"ephemeral", "historyMode"}
                    }
                    runtime.resume_thread(row["codex_thread_id"], overrides)
                else:
                    result = runtime.start_thread(payload)
                    thread = result.get("thread") or {}
                    thread_id = thread.get("id")
                    if not thread_id:
                        raise CodexAppServerError("Codex 未返回 thread id")
                    self.db.update_conversation(conversation_id, {"codex_thread_id": thread_id})
                    row["codex_thread_id"] = thread_id
            except Exception:
                runtime.close()
                raise
            with self._lock:
                self._runtimes[conversation_id] = runtime
            return runtime

    def _run_turn(
        self,
        conversation_id: str,
        text: str,
        mentions: list[dict[str, Any]],
        generation: int,
        lock: threading.Lock,
    ) -> None:
        try:
            row = self._conversation(conversation_id)
            if row.get("status") != "running" or int(row.get("turn_generation") or 0) != generation:
                return
            runtime = self._ensure_runtime(row)
            if not row.get("codex_thread_id"):
                row = self._conversation(conversation_id)
            result = runtime.start_turn(
                {
                    "threadId": row["codex_thread_id"],
                    "cwd": row.get("cwd") or str(self.settings.workspace),
                    "input": build_input(text, mentions),
                    "model": row.get("model"),
                    "effort": row.get("reasoning_effort"),
                    "approvalPolicy": "on-request",
                    "approvalsReviewer": "user",
                    "sandboxPolicy": {"type": "readOnly", "networkAccess": True},
                    "clientUserMessageId": uuid.uuid4().hex,
                }
            )
            turn = result.get("turn") or {}
            turn_id = turn.get("id")
            if turn_id:
                current = self._conversation(conversation_id)
                if current.get("status") != "running" or int(current.get("turn_generation") or 0) != generation:
                    try:
                        runtime.interrupt(row["codex_thread_id"], turn_id)
                    except Exception:
                        logger.warning("Failed to interrupt stale turn", exc_info=True)
                    return
                self.db.update_conversation(conversation_id, {"active_turn_id": turn_id})
                self.db.append_event(
                    conversation_id,
                    "turn.accepted",
                    {"turn_id": turn_id, "turn_generation": generation},
                )
        except Exception as exc:
            logger.exception("Codex turn failed")
            self._mark_failed(conversation_id, str(exc), generation)
        finally:
            if lock.locked():
                try:
                    lock.release()
                except RuntimeError:
                    pass

    def _on_runtime_exit(
        self,
        conversation_id: str,
        runtime: CodexAppServer,
        detail: str,
    ) -> None:
        with self._lock:
            if self._runtimes.get(conversation_id) is runtime:
                self._runtimes.pop(conversation_id, None)
        try:
            row = self._conversation(conversation_id)
        except ConversationNotFound:
            return
        self._cancel_interactions(conversation_id)
        if row.get("status") == "running":
            self._mark_failed(
                conversation_id,
                f"智能中枢运行时意外退出：{detail}",
                int(row.get("turn_generation") or 0),
            )

    def _mark_failed(self, conversation_id: str, message: str, generation: int | None = None) -> bool:
        current = self.db.get_conversation(conversation_id)
        if not current:
            return False
        if generation is not None and (
            current.get("status") != "running" or int(current.get("turn_generation") or 0) != generation
        ):
            return False
        self.db.update_conversation(
            conversation_id,
            {"status": "failed", "last_error": message, "active_turn_id": None},
        )
        self.db.append_event(
            conversation_id,
            "error",
            {"message": message, "turn_generation": generation},
        )
        return True

    @staticmethod
    def _safe_payload(value: Any, depth: int = 0) -> Any:
        if depth > 8:
            return "[truncated]"
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return value[:50_000]
        if isinstance(value, list):
            return [CodexHub._safe_payload(item, depth + 1) for item in value[:300]]
        if isinstance(value, dict):
            return {str(key): CodexHub._safe_payload(item, depth + 1) for key, item in value.items()}
        return str(value)

    def _on_notification(self, conversation_id: str, method: str, params: dict[str, Any]) -> None:
        safe_params = self._safe_payload(params)
        kind = event_kind(method)
        item_id = extract_item_id(safe_params)

        if method in {"item/started", "item/completed"}:
            item = safe_params.get("item") if isinstance(safe_params.get("item"), dict) else safe_params
            item_type = normalize_item_type(item)
            if item_type and item_type != "userMessage" and item_id:
                self.db.upsert_codex_item(
                    conversation_id,
                    item_id,
                    {
                        "item_type": item_type,
                        "role": "assistant" if item_type == "agentMessage" else None,
                        "status": "completed" if method == "item/completed" else "inProgress",
                        "payload": item,
                    },
                )
        delta_type = item_type_for_method(method)
        if delta_type and item_id:
            existing = self.db.get_codex_item(conversation_id, item_id)
            payload = accumulate_payload(
                delta_type,
                (existing or {}).get("payload") or {},
                safe_params,
            )
            self.db.upsert_codex_item(
                conversation_id,
                item_id,
                {
                    "item_type": delta_type,
                    "role": "assistant" if delta_type == "agentMessage" else None,
                    "status": "inProgress",
                    "payload": payload,
                },
            )

        if method == "turn/started":
            turn = safe_params.get("turn") if isinstance(safe_params.get("turn"), dict) else {}
            if turn.get("id"):
                self.db.update_conversation(conversation_id, {"active_turn_id": str(turn["id"]), "status": "running"})
        elif method == "turn/completed":
            turn = safe_params.get("turn") if isinstance(safe_params.get("turn"), dict) else {}
            status = str(turn.get("status") or "completed")
            current = self._conversation(conversation_id)
            completed_turn_id = str(turn.get("id") or "")
            active_turn_id = str(current.get("active_turn_id") or "")
            if not active_turn_id or not completed_turn_id or active_turn_id == completed_turn_id:
                self.db.update_conversation(
                    conversation_id,
                    {
                        "status": "failed" if status == "failed" else "idle",
                        "active_turn_id": None,
                        "last_error": (flatten_text(turn) if status == "failed" else None),
                    },
                )
        elif method == "error":
            message = str(
                safe_params.get("message") or (safe_params.get("error") or {}).get("message") or "Codex runtime error"
            )
            self.db.update_conversation(conversation_id, {"last_error": message})
        elif method == "thread/name/updated":
            title = str(safe_params.get("name") or safe_params.get("title") or "").strip()
            if title:
                self.db.update_conversation(conversation_id, {"title": title})

        item = safe_params.get("item") if isinstance(safe_params.get("item"), dict) else {}
        if normalize_item_type(item) == "userMessage":
            return
        self.db.append_event(
            conversation_id,
            kind,
            {"method": method, "params": safe_params, "item_id": item_id},
        )

    def _on_server_request(self, conversation_id: str, message: dict[str, Any]) -> Any:
        method = str(message.get("method") or "")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        request_id = str(message.get("id") or "")
        if method == "item/tool/requestUserInput":
            return self._handle_clarification(conversation_id, request_id, method, params)
        if method == "mcpServer/elicitation/request":
            return self._handle_clarification(conversation_id, request_id, method, params)
        if method == "item/tool/call":
            return self._handle_tool(conversation_id, params)
        if method in APPROVAL_METHODS:
            return self._handle_approval(conversation_id, request_id, method, params)
        if method == "account/chatgptAuthTokens/refresh":
            return self._refresh_auth_tokens()
        if method == "attestation/generate":
            raise ValueError("当前客户端不生成上游 attestation token")
        return {}

    @staticmethod
    def _refresh_auth_tokens() -> dict[str, Any]:
        codex_home = Path(os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")).expanduser()
        auth_path = codex_home / "auth.json"
        if not auth_path.is_file():
            raise ValueError("Codex 登录状态需要刷新，请重新执行 codex login")
        try:
            payload = json.loads(auth_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("无法读取 Codex 登录状态，请重新登录") from exc
        if not isinstance(payload, dict):
            raise ValueError("Codex 登录状态格式无效")
        tokens = payload.get("tokens") if isinstance(payload.get("tokens"), dict) else payload
        access_token = str(tokens.get("access_token") or tokens.get("accessToken") or payload.get("access_token") or "")
        account_id = str(
            tokens.get("account_id") or tokens.get("chatgpt_account_id") or payload.get("chatgpt_account_id") or ""
        )
        plan_type = tokens.get("plan_type") or tokens.get("chatgpt_plan_type") or payload.get("chatgpt_plan_type")
        if not access_token or not account_id:
            raise ValueError("Codex 登录状态缺少可刷新的 Token，请重新登录")
        return {
            "accessToken": access_token,
            "chatgptAccountId": account_id,
            "chatgptPlanType": plan_type,
        }

    def _handle_tool(self, conversation_id: str, params: dict[str, Any]) -> dict[str, Any]:
        name = str(params.get("tool") or "")
        namespace = str(params.get("namespace") or "").strip("/")
        if namespace and not name.startswith(f"{namespace}/"):
            name = f"{namespace}/{name}"
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        result = self.tools.execute(
            name,
            arguments,
            {"conversation_id": conversation_id},
        )
        self.db.record_tool_run(
            conversation_id,
            str(params.get("turnId") or "") or None,
            str(params.get("callId") or "") or None,
            name,
            arguments,
            result,
        )
        self.db.append_event(
            conversation_id,
            "tool.completed",
            {
                "name": name,
                "arguments": self._safe_payload(arguments),
                "result": self._safe_payload(result),
            },
        )
        return result

    def _handle_approval(
        self,
        conversation_id: str,
        request_id: str,
        method: str,
        params: dict[str, Any],
    ) -> Any:
        row = self._conversation(conversation_id)
        request = classify_approval(method, params, row.get("cwd") or str(self.settings.workspace))
        record = self.db.create_approval(
            conversation_id,
            request_id,
            method,
            request,
            self._safe_payload(params),
        )
        waiter = {"event": threading.Event(), "decision": "decline"}
        self._approval_waiters[record["id"]] = waiter
        self.db.append_event(conversation_id, "approval.requested", record)
        waiter["event"].wait(self.settings.approval_timeout_seconds)
        self._approval_waiters.pop(record["id"], None)
        current = self.db.get_approval(record["id"]) or record
        decision = str(current.get("decision") or waiter["decision"] or "decline")
        if not current.get("decision"):
            self.db.resolve_approval(record["id"], decision, "审批等待超时")
            self.db.append_event(
                conversation_id,
                "approval.resolved",
                {"approval_id": record["id"], "decision": decision, "timeout": True},
            )
        return approval_response(method, decision, params)

    def _handle_clarification(
        self,
        conversation_id: str,
        request_id: str,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        questions = question_list(params)
        if not questions:
            questions = [
                {
                    "id": "response",
                    "header": "需要补充",
                    "question": str(params.get("message") or "请补充所需信息"),
                    "required": True,
                    "type": "string",
                    "options": [],
                }
            ]
        record = self.db.create_clarification(
            conversation_id,
            request_id,
            "mcp" if method.startswith("mcpServer/") else "question",
            questions,
        )
        waiter = {"event": threading.Event(), "answers": {}}
        self._clarification_waiters[record["id"]] = waiter
        self.db.append_event(conversation_id, "clarification.requested", record)
        waiter["event"].wait(self.settings.approval_timeout_seconds)
        self._clarification_waiters.pop(record["id"], None)
        current = self.db.get_clarification(record["id"]) or record
        answers = current.get("answers") or waiter["answers"] or {}
        if current.get("answers") is None:
            self.db.resolve_clarification(record["id"], {})
        if method.startswith("mcpServer/"):
            return {"action": "accept", "content": answers} if answers else {"action": "decline"}
        normalized: dict[str, dict[str, list[str]]] = {}
        for key, value in answers.items():
            normalized[str(key)] = {"answers": value if isinstance(value, list) else [str(value)]}
        return {"answers": normalized}

    def _cancel_interactions(self, conversation_id: str) -> None:
        for approval in self.db.pending_approvals(conversation_id):
            waiter = self._approval_waiters.get(approval["id"])
            self.db.resolve_approval(approval["id"], "cancel", "对话已中断")
            if waiter:
                waiter["decision"] = "cancel"
                waiter["event"].set()
        for clarification in self.db.pending_clarifications(conversation_id):
            waiter = self._clarification_waiters.get(clarification["id"])
            self.db.resolve_clarification(clarification["id"], {})
            if waiter:
                waiter["event"].set()
