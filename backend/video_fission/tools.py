from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .db import Database


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
    return value[:60] or datetime.now(UTC).strftime("brief-%Y%m%d-%H%M%S")


def build_registry(db: Database, workspace: Path, artifacts_dir: Path) -> ToolRegistry:
    registry = ToolRegistry(db, workspace.resolve(), artifacts_dir.resolve())

    def workspace_snapshot(arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        root = _safe_relative(str(arguments.get("path") or "."), registry.workspace)
        limit = min(max(int(arguments.get("limit") or 80), 1), 200)
        entries: list[dict[str, Any]] = []
        for path in sorted(root.rglob("*")):
            if len(entries) >= limit:
                break
            if ".git" in path.parts:
                continue
            entries.append(
                {
                    "path": str(path.relative_to(registry.workspace)),
                    "kind": "directory" if path.is_dir() else "file",
                    "size": path.stat().st_size if path.is_file() else None,
                }
            )
        return {
            "workspace": str(registry.workspace),
            "entries": entries,
            "truncated": len(entries) >= limit,
        }

    def read_workspace_file(arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        path = _safe_relative(str(arguments.get("path") or ""), registry.workspace)
        if not path.is_file():
            raise ValueError("File does not exist")
        if path.stat().st_size > 200_000:
            raise ValueError("File is larger than 200 KB")
        return {
            "path": str(path.relative_to(registry.workspace)),
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
        topic = str(arguments.get("topic") or "未命名视频").strip()
        audience = str(arguments.get("audience") or "泛内容受众").strip()
        duration = int(arguments.get("duration_seconds") or 30)
        angle = str(arguments.get("angle") or "高信息密度、强节奏的开场").strip()
        conversation_id = str(context.get("conversation_id") or "shared")
        folder = registry.artifacts_dir / conversation_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{_slug(topic)}.md"
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
| 0-3s | 主体特写 + 动态字标 | 结果先行 | 快切 |
| 3-{max(8, duration // 3)}s | 三段对比蒙太奇 | 逐条拆解 | 节拍点切 |
| {max(8, duration // 3)}-{max(18, duration * 2 // 3)}s | 证据特写 | 数据与细节 | 稳态推进 |
| 尾段 | 回扣开场 + CTA | 下一步行动 | 定格 |

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
                    "path": {"type": "string", "description": "Relative directory path"},
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
                "properties": {"path": {"type": "string", "description": "Relative file path"}},
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
                    "topic": {"type": "string"},
                    "audience": {"type": "string"},
                    "angle": {"type": "string"},
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
    return registry
