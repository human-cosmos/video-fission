from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .db import Database
from .paths import conversation_artifact_dir


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]

    def spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


class ToolRegistry:
    def __init__(
        self,
        db: Database,
        workspace: Path,
        artifacts_dir: Path,
    ):
        self.db = db
        self.workspace = workspace
        self.artifacts_dir = artifacts_dir
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def all(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def specs(self) -> list[dict[str, Any]]:
        return [tool.spec() for tool in self._tools.values()]

    def summary(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "title": tool.title,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in self._tools.values()
        ]

    def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        tool = self._tools.get(name)
        if not tool:
            return {
                "success": False,
                "contentItems": [{"type": "inputText", "text": f"Unknown tool: {name}"}],
            }
        try:
            result = tool.handler(arguments, context)
            return {
                "success": True,
                "contentItems": [
                    {
                        "type": "inputText",
                        "text": result.get("text") or json.dumps(result, ensure_ascii=False, indent=2),
                    }
                ],
            }
        except Exception as exc:
            return {
                "success": False,
                "contentItems": [{"type": "inputText", "text": f"{type(exc).__name__}: {exc}"}],
            }


def _safe_relative(path: str, workspace: Path) -> Path:
    candidate = (workspace / path.strip()).resolve()
    if candidate != workspace and workspace not in candidate.parents:
        raise ValueError("Path must stay inside the workspace")
    return candidate


def _slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    return f"{value[:45] or 'brief'}-{stamp}"


def build_registry(db: Database, workspace: Path, artifacts_dir: Path, productions=None) -> ToolRegistry:
    registry = ToolRegistry(db, workspace.resolve(), artifacts_dir.resolve())

    def conversation_row(context: dict[str, Any]) -> dict[str, Any]:
        conversation_id = str(context.get("conversation_id") or "")
        row = db.get_conversation(conversation_id)
        if not row:
            raise ValueError("当前对话不存在")
        return row

    def conversation_user(context: dict[str, Any]) -> str | None:
        conversation_id = str(context.get("conversation_id") or "")
        row = db.get_conversation(conversation_id) if conversation_id else None
        return str((row or {}).get("user_id") or "").strip() or None

    def conversation_workspace(context: dict[str, Any]) -> Path:
        row = conversation_row(context)
        return Path(str(row.get("cwd") or registry.workspace)).expanduser().resolve()

    def workspace_snapshot(arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        workspace_root = conversation_workspace(context)
        root = _safe_relative(str(arguments.get("path") or "."), workspace_root)
        limit = min(max(int(arguments.get("limit") or 80), 1), 200)
        entries: list[dict[str, Any]] = []
        truncated = False
        for path in root.rglob("*"):
            if ".git" in path.parts:
                continue
            if len(entries) >= limit:
                truncated = True
                break
            try:
                resolved = path.resolve()
                if resolved != workspace_root and workspace_root not in resolved.parents:
                    continue
                kind = "directory" if resolved.is_dir() else "file" if resolved.is_file() else "other"
                size = resolved.stat().st_size if kind == "file" else None
            except OSError:
                continue
            entries.append(
                {
                    "path": str(resolved.relative_to(workspace_root)),
                    "kind": kind,
                    "size": size,
                }
            )
        return {
            "workspace": str(workspace_root),
            "entries": entries,
            "truncated": truncated,
        }

    def read_workspace_file(arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        workspace_root = conversation_workspace(context)
        path = _safe_relative(str(arguments.get("path") or ""), workspace_root)
        if not path.is_file():
            raise ValueError("File does not exist")
        if path.stat().st_size > 200_000:
            raise ValueError("File is larger than 200 KB")
        return {
            "path": str(path.relative_to(workspace_root)),
            "text": path.read_text(encoding="utf-8", errors="replace"),
        }

    def conversation_metrics(arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        conversation_id = str(context.get("conversation_id") or "")
        rows = registry.db.query(
            "SELECT item_type, COUNT(*) AS count FROM items WHERE conversation_id = ? GROUP BY item_type",
            (conversation_id,),
        )
        tool_count = registry.db.query_one(
            "SELECT COUNT(*) AS count FROM tool_runs WHERE conversation_id = ?",
            (conversation_id,),
        )
        return {
            "conversation_id": conversation_id,
            "item_types": {row["item_type"]: row["count"] for row in rows},
            "tool_runs": int(tool_count["count"]) if tool_count else 0,
        }

    def create_video_brief(arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        topic = str(arguments.get("topic") or "未命名视频").strip()[:200]
        audience = str(arguments.get("audience") or "泛内容受众").strip()[:200]
        duration = min(max(int(arguments.get("duration_seconds") or 30), 5), 600)
        angle = str(arguments.get("angle") or "高信息密度、强节奏的开场").strip()[:500]
        conversation_id = str(context.get("conversation_id") or "shared")
        row = conversation_row(context)
        folder = conversation_artifact_dir(
            registry.artifacts_dir,
            row.get("user_id"),
            conversation_id,
        )
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{_slug(topic)}.md"
        hook_end = min(3, max(1, duration // 4))
        middle_start = hook_end
        middle_end = max(middle_start + 1, duration * 2 // 3)
        middle_end = min(middle_end, max(middle_start + 1, duration - 1))
        body = f"""# {topic}

- 目标受众：{audience}
- 预计时长：{duration} 秒
- 叙事切口：{angle}

## 0-3 秒钩子

用一个反常识结论或高密度视觉冲突建立停留理由。

## 叙事骨架

1. 钩子：先说观众能立即感知的结果。
2. 裂变：把结果拆成三个可验证的细节。
3. 证据：用画面、数字或对比支撑每个细节。
4. 收束：给出一个可执行的下一步。

## 镜头清单

| 时间 | 画面 | 字幕/旁白 | 节奏 |
| --- | --- | --- | --- |
| 0-{hook_end}s | 主体特写 + 动态字标 | 结果先行 | 快切 |
| {middle_start}-{middle_end}s | 三段对比蒙太奇 | 逐条拆解 | 节拍点切 |
| {middle_end}-{duration}s | 证据特写与 CTA | 数据、细节与行动 | 稳态收束 |

## 制作备注

- 每个信息点只保留一个视觉焦点。
- 关键句不超过 14 个汉字。
- 结尾 CTA 与开场钩子使用同一视觉母题。
"""
        path.write_text(body, encoding="utf-8")
        return {
            "path": str(path),
            "relative_path": str(path.relative_to(registry.artifacts_dir)),
            "summary": f"已生成《{topic}》视频简报",
            "text": f"已生成视频简报：{path}",
        }

    registry.register(
        ToolDefinition(
            name="workspace_snapshot",
            title="工作区快照",
            description=(
                "List files and directories in the isolated Video Fission workspace. "
                "Use this when the user asks what files are available."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "maxLength": 500,
                        "description": "Relative directory path",
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                "additionalProperties": False,
            },
            handler=workspace_snapshot,
        )
    )
    registry.register(
        ToolDefinition(
            name="read_workspace_file",
            title="读取工作区文件",
            description="Read a UTF-8 text file inside the isolated workspace.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "maxLength": 500,
                        "description": "Relative file path",
                    }
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=read_workspace_file,
        )
    )
    registry.register(
        ToolDefinition(
            name="conversation_metrics",
            title="会话指标",
            description=(
                "Read persisted item and tool-run counts for the current conversation. "
                "Use this when the user asks for conversation statistics."
            ),
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=conversation_metrics,
        )
    )
    registry.register(
        ToolDefinition(
            name="create_video_brief",
            title="生成视频简报",
            description=(
                "Create a reusable markdown video brief in the application artifact store. "
                "Use this whenever the user asks to save, generate, or export a video brief."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "maxLength": 200},
                    "audience": {"type": "string", "maxLength": 200},
                    "angle": {"type": "string", "maxLength": 500},
                    "duration_seconds": {
                        "type": "integer",
                        "minimum": 5,
                        "maximum": 600,
                    },
                },
                "required": ["topic"],
                "additionalProperties": False,
            },
            handler=create_video_brief,
        )
    )

    def start_video_production(arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        if productions is None:
            raise ValueError("视频创作管线尚未就绪")
        brief = str(arguments.get("brief") or "").strip()
        upload_id = str(arguments.get("video_upload_id") or "").strip() or None
        source_path = str(arguments.get("source_path") or arguments.get("path") or "").strip() or None
        policy = arguments.get("replace_policy")
        conversation_id = str(arguments.get("conversation_id") or context.get("conversation_id") or "").strip() or None
        user_id = conversation_user(context)
        if conversation_id:
            owned = db.get_conversation(conversation_id, user_id)
            if not owned:
                raise ValueError("目标对话不存在")
        call_kwargs: dict[str, Any] = {}
        if user_id is not None:
            call_kwargs["user_id"] = user_id
        result = productions.start_from_tool(
            brief,
            video_upload_id=upload_id,
            source_path=source_path,
            replace_policy=policy if isinstance(policy, dict) else None,
            conversation_id=conversation_id,
            **call_kwargs,
        )
        return {
            "production": result,
            "message": f"已启动创作任务 {result['id']}，状态 {result['status']}",
        }

    def get_video_production(arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        if productions is None:
            raise ValueError("视频创作管线尚未就绪")
        production_id = str(arguments.get("production_id") or arguments.get("id") or "").strip()
        user_id = conversation_user(context)
        result = (
            productions.get(production_id, user_id=user_id)
            if user_id is not None
            else productions.get(production_id)
        )
        return {"production": result}

    def run_video_production(arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        if productions is None:
            raise ValueError("视频创作管线尚未就绪")
        production_id = str(arguments.get("production_id") or arguments.get("id") or "").strip()
        policy = arguments.get("replace_policy")
        resume_from = arguments.get("resume_from")
        user_id = conversation_user(context)
        run_kwargs: dict[str, Any] = {
            "replace_policy": policy if isinstance(policy, dict) else None,
            "resume_from": str(resume_from).strip() if resume_from else None,
        }
        if user_id is not None:
            run_kwargs["user_id"] = user_id
        result = productions.run(production_id, **run_kwargs)
        return {
            "production": result,
            "message": f"已重跑创作任务 {result['id']}，状态 {result['status']}",
        }

    registry.register(
        ToolDefinition(
            name="start_video_production",
            title="开始视频创作",
            description=(
                "Create and start a video production from a creative brief and a video_upload_id "
                "from a video uploaded in the conversation. An allowed local source_path also works. "
                "If this conversation already started a matching task, return that existing production "
                "instead of creating a duplicate."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "brief": {"type": "string", "maxLength": 20000},
                    "video_upload_id": {"type": "string", "maxLength": 80},
                    "source_path": {"type": "string", "maxLength": 500},
                    "conversation_id": {"type": "string", "maxLength": 80},
                    "replace_policy": {
                        "type": "object",
                        "properties": {
                            "mode": {"type": "string"},
                            "roles": {"type": "array", "items": {"type": "string"}},
                            "segment_ids": {"type": "array", "items": {"type": "string"}},
                            "time_ranges": {"type": "array"},
                        },
                    },
                },
                "required": ["brief"],
                "additionalProperties": False,
            },
            handler=start_video_production,
        )
    )
    registry.register(
        ToolDefinition(
            name="get_video_production",
            title="查询视频创作",
            description="Look up a video production's status, stages, and artifact paths.",
            input_schema={
                "type": "object",
                "properties": {
                    "production_id": {"type": "string", "maxLength": 80},
                },
                "required": ["production_id"],
                "additionalProperties": False,
            },
            handler=get_video_production,
        )
    )
    registry.register(
        ToolDefinition(
            name="run_video_production",
            title="重跑视频创作",
            description=(
                "Re-run an existing video production with the same POST /run body: "
                "optional resume_from (ingest|decompose|intent) and replace_policy. "
                "Use this when the conversation already has a production_id and the user wants a follow-up."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "production_id": {"type": "string", "maxLength": 80},
                    "resume_from": {"type": "string", "maxLength": 20},
                    "replace_policy": {
                        "type": "object",
                        "properties": {
                            "mode": {"type": "string"},
                            "roles": {"type": "array", "items": {"type": "string"}},
                            "segment_ids": {"type": "array", "items": {"type": "string"}},
                            "time_ranges": {"type": "array"},
                        },
                    },
                },
                "required": ["production_id"],
                "additionalProperties": False,
            },
            handler=run_video_production,
        )
    )
    return registry
