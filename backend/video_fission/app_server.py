from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class CodexAppServerError(RuntimeError):
    pass


class CodexAppServer:
    def __init__(
        self,
        codex_bin: str,
        *,
        cwd: Path | str,
        timeout_seconds: int = 180,
        on_notification: Callable[[str, dict[str, Any]], None] | None = None,
        on_server_request: Callable[[dict[str, Any]], Any] | None = None,
        on_exit: Callable[[CodexAppServer, str], None] | None = None,
    ):
        self.codex_bin = codex_bin
        self.cwd = str(cwd)
        self.timeout_seconds = timeout_seconds
        self.on_notification = on_notification
        self.on_server_request = on_server_request
        self.on_exit = on_exit
        self.proc: subprocess.Popen[str] | None = None
        self._next_id = 1
        self._pending: dict[int, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._closed = False
        self.stderr = ""
        self.last_activity = 0.0

    @property
    def running(self) -> bool:
        return bool(self.proc and self.proc.poll() is None and not self._closed)

    def start(self) -> CodexAppServer:
        is_shell = os.name == "nt" and self.codex_bin.lower().endswith((".cmd", ".bat"))
        command = f'"{self.codex_bin}" app-server --stdio' if is_shell else [self.codex_bin, "app-server", "--stdio"]
        try:
            self.proc = subprocess.Popen(
                command,
                cwd=self.cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=is_shell,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                start_new_session=os.name != "nt",
            )
        except OSError as exc:
            raise CodexAppServerError(f"无法启动智能中枢运行时: {exc}") from exc
        thread = threading.Thread(target=self._read_loop, daemon=True, name="codex-stdout")
        thread.start()
        stderr_thread = threading.Thread(target=self._read_stderr, daemon=True, name="codex-stderr")
        stderr_thread.start()
        return self

    def _read_stderr(self) -> None:
        if not self.proc or not self.proc.stderr:
            return
        for line in self.proc.stderr:
            text = line.rstrip()
            if text:
                self.stderr = text[-4000:]

    def _read_loop(self) -> None:
        if not self.proc or not self.proc.stdout:
            return
        for line in self.proc.stdout:
            self.last_activity = time.time()
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Discarded non-JSON Codex output: %s", line[:300])
                continue
            self._handle_message(message)
        with self._lock:
            if self._closed:
                return
            detail = self.stderr or (
                f"智能中枢运行时异常退出（代码 {self.proc.poll()}）" if self.proc else "智能中枢运行时已退出"
            )
            for waiter in self._pending.values():
                waiter["error"] = detail
                waiter["event"].set()
            self._pending.clear()
            callback = self.on_exit
        if callback:
            try:
                callback(self, detail)
            except Exception:
                logger.exception("Codex app-server exit handler failed")

    def _handle_message(self, message: dict[str, Any]) -> None:
        if "id" in message and ("result" in message or "error" in message):
            with self._lock:
                waiter = self._pending.pop(int(message["id"]), None)
            if waiter:
                waiter["result"] = message.get("result")
                waiter["error"] = message.get("error")
                waiter["event"].set()
            return
        method = str(message.get("method") or "")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if "id" in message:
            threading.Thread(
                target=self._answer_server_request,
                args=(message,),
                daemon=True,
                name=f"codex-request-{method}",
            ).start()
            return
        if method and self.on_notification:
            try:
                self.on_notification(method, params)
            except Exception:
                logger.exception("Codex notification handler failed: %s", method)

    def _answer_server_request(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        try:
            result = self.on_server_request(message) if self.on_server_request else None
            response = {"id": request_id, "result": result if result is not None else {}}
        except Exception as exc:
            logger.exception("Codex server request failed")
            response = {
                "id": request_id,
                "error": {"code": -32000, "message": str(exc)},
            }
        try:
            self._send(response)
        except CodexAppServerError:
            if not self._closed:
                logger.warning("Could not answer Codex server request", exc_info=True)

    def _send(self, message: dict[str, Any]) -> None:
        if not self.proc or not self.proc.stdin:
            raise CodexAppServerError("智能中枢运行时尚未启动")
        encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            self.proc.stdin.write(encoded)
            self.proc.stdin.flush()

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"method": method}
        if params is not None:
            payload["params"] = params
        self._send(payload)

    def rpc(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: int | None = None,
    ) -> Any:
        if not self.running:
            raise CodexAppServerError(self.stderr or "智能中枢运行时未运行")
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
            waiter = {"event": threading.Event(), "result": None, "error": None}
            self._pending[request_id] = waiter
        try:
            self._send({"method": method, "id": request_id, "params": params or {}})
        except Exception:
            with self._lock:
                self._pending.pop(request_id, None)
            raise
        timeout = timeout_seconds or self.timeout_seconds
        if not waiter["event"].wait(timeout):
            with self._lock:
                self._pending.pop(request_id, None)
            raise CodexAppServerError(f"{method} 在 {timeout}s 后超时")
        if waiter["error"]:
            error = waiter["error"]
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise CodexAppServerError(f"{method}: {message}")
        return waiter["result"]

    def initialize(self) -> dict[str, Any]:
        result = self.rpc(
            "initialize",
            {
                "clientInfo": {
                    "name": "video-fission",
                    "title": "Video Fission",
                    "version": "0.1.0",
                },
                "capabilities": {"experimentalApi": True},
            },
        )
        self.notify("initialized", {})
        return result or {}

    def set_skill_roots(self, roots: list[str]) -> None:
        self.rpc("skills/extraRoots/set", {"extraRoots": roots})

    def list_skills(self, cwds: list[str], *, refresh: bool = False) -> dict[str, Any]:
        return self.rpc("skills/list", {"cwds": cwds, "forceReload": refresh}) or {}

    def start_thread(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = {key: value for key, value in payload.items() if value is not None}
        payload.setdefault("historyMode", "paginated")
        return self.rpc("thread/start", payload) or {}

    def resume_thread(self, thread_id: str, overrides: dict[str, Any]) -> dict[str, Any]:
        payload = {"threadId": thread_id, "excludeTurns": True, **overrides}
        return self.rpc("thread/resume", payload) or {}

    def fork_thread(self, thread_id: str) -> dict[str, Any]:
        return (
            self.rpc(
                "thread/fork",
                {"threadId": thread_id, "excludeTurns": True},
            )
            or {}
        )

    def set_thread_name(self, thread_id: str, name: str) -> Any:
        return self.rpc("thread/name/set", {"threadId": thread_id, "name": name})

    def archive_thread(self, thread_id: str, archived: bool) -> Any:
        return self.rpc("thread/archive" if archived else "thread/unarchive", {"threadId": thread_id})

    def start_turn(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.rpc("turn/start", payload) or {}

    def steer_turn(self, thread_id: str, turn_id: str, items: list[dict[str, Any]]) -> Any:
        return self.rpc(
            "turn/steer",
            {"threadId": thread_id, "expectedTurnId": turn_id, "input": items},
        )

    def interrupt(self, thread_id: str, turn_id: str) -> Any:
        return self.rpc("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})

    def compact(self, thread_id: str) -> Any:
        return self.rpc("thread/compact/start", {"threadId": thread_id})

    def list_models(self) -> dict[str, Any]:
        return self.rpc("model/list", {"includeHidden": False, "limit": 100}) or {}

    def list_all_items(self, thread_id: str) -> list[dict[str, Any]]:
        cursor: str | None = None
        items: list[dict[str, Any]] = []
        for _ in range(100):
            payload = (
                self.rpc(
                    "thread/items/list",
                    {
                        "threadId": thread_id,
                        "limit": 100,
                        "sortDirection": "asc",
                        **({"cursor": cursor} if cursor else {}),
                    },
                )
                or {}
            )
            items.extend(payload.get("data") or [])
            cursor = payload.get("nextCursor")
            if not cursor:
                break
        return items

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        proc = self.proc
        if not proc:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
