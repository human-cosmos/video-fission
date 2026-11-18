from __future__ import annotations

import re
import shutil
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml


class SkillValidationError(ValueError):
    pass


def _slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if not value:
        value = f"skill-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"
    return value[:64]


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    text = text.lstrip("\ufeff")
    if not text.startswith("---"):
        raise SkillValidationError("SKILL.md 必须以 YAML frontmatter 开头")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise SkillValidationError("SKILL.md 的 frontmatter 未闭合")
    metadata = yaml.safe_load(parts[1]) or {}
    if not isinstance(metadata, dict):
        raise SkillValidationError("SKILL.md frontmatter 必须是键值对象")
    return metadata, parts[2].strip()


def parse_skill(path: Path) -> dict[str, Any]:
    skill_file = path / "SKILL.md"
    text = skill_file.read_text(encoding="utf-8")
    metadata, body = _split_frontmatter(text)
    name = str(metadata.get("name") or path.name).strip()
    description = str(metadata.get("description") or "").strip()
    if not name or not description:
        raise SkillValidationError("SKILL.md 需要 name 和 description")
    return {
        "slug": path.name,
        "name": name,
        "description": description,
        "content": body,
        "raw": text,
        "path": str(skill_file),
        "updated_at": datetime.fromtimestamp(skill_file.stat().st_mtime, tz=UTC).isoformat(),
    }


class SkillManager:
    def __init__(self, user_root: Path, bundled_root: Path, disabled_root: Path):
        self.user_root = user_root
        self.bundled_root = bundled_root
        self.disabled_root = disabled_root
        self._mutation_lock = threading.RLock()
        for path in (self.user_root, self.disabled_root):
            path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _write_skill_file(destination: Path, content: str) -> None:
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)

    @property
    def runtime_roots(self) -> list[str]:
        roots = [self.user_root.resolve()]
        if self.bundled_root.exists():
            roots.append(self.bundled_root.resolve())
        return [str(path) for path in roots]

    def _rows(self, root: Path, source: str, enabled: bool) -> list[dict[str, Any]]:
        if not root.exists():
            return []
        rows: list[dict[str, Any]] = []
        for entry in sorted(root.iterdir()):
            if not entry.is_dir() or not (entry / "SKILL.md").is_file():
                continue
            try:
                skill = parse_skill(entry)
            except (OSError, SkillValidationError, yaml.YAMLError) as exc:
                rows.append(
                    {
                        "slug": entry.name,
                        "name": entry.name,
                        "description": f"无法读取：{exc}",
                        "enabled": enabled,
                        "source": source,
                        "error": str(exc),
                    }
                )
                continue
            rows.append(
                {
                    **skill,
                    "enabled": enabled,
                    "source": source,
                    "read_only": source == "bundled",
                }
            )
        return rows

    def list(self) -> list[dict[str, Any]]:
        rows = [
            *self._rows(self.bundled_root, "bundled", True),
            *self._rows(self.user_root, "user", True),
            *self._rows(self.disabled_root, "user", False),
        ]
        return sorted(rows, key=lambda row: (row["source"], row["name"].lower()))

    def get(self, slug: str) -> dict[str, Any]:
        safe = _slug(slug)
        for root, source, enabled in (
            (self.user_root, "user", True),
            (self.disabled_root, "user", False),
            (self.bundled_root, "bundled", True),
        ):
            path = root / safe
            if path.is_dir() and (path / "SKILL.md").is_file():
                return {
                    **parse_skill(path),
                    "enabled": enabled,
                    "source": source,
                    "read_only": source == "bundled",
                }
        raise KeyError("Skill not found")

    @staticmethod
    def render(name: str, description: str, body: str) -> str:
        name = name.strip()
        description = description.strip()
        body = body.strip()
        if not name or not description:
            raise SkillValidationError("名称和描述不能为空")
        if len(description) > 500:
            raise SkillValidationError("描述不能超过 500 字")
        if not body:
            raise SkillValidationError("Skill 正文不能为空")
        frontmatter = yaml.safe_dump(
            {"name": name, "description": description},
            allow_unicode=True,
            sort_keys=False,
        ).strip()
        return f"---\n{frontmatter}\n---\n\n{body}\n"

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._mutation_lock:
            name = str(payload.get("name") or "").strip()
            slug = _slug(str(payload.get("slug") or name))
            destination = self.user_root / slug
            if (
                destination.exists()
                or (self.disabled_root / slug).exists()
                or (self.bundled_root / slug).exists()
            ):
                raise SkillValidationError("Skill 标识已存在")
            rendered = self.render(
                name,
                str(payload.get("description") or ""),
                str(payload.get("body") or ""),
            )
            destination.mkdir(parents=True)
            try:
                self._write_skill_file(destination / "SKILL.md", rendered)
            except Exception:
                shutil.rmtree(destination, ignore_errors=True)
                raise
            return self.get(slug)

    def update(self, slug: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._mutation_lock:
            existing = self.get(slug)
            if existing["read_only"]:
                raise SkillValidationError("内置 Skill 为只读")
            folder = Path(existing["path"]).parent
            self._write_skill_file(
                folder / "SKILL.md",
                self.render(
                    str(payload.get("name") or existing["name"]),
                    str(payload.get("description") or existing["description"]),
                    str(payload.get("body") if "body" in payload else existing["content"]),
                ),
            )
            return self.get(slug)

    def toggle(self, slug: str, enabled: bool) -> dict[str, Any]:
        with self._mutation_lock:
            existing = self.get(slug)
            if existing["read_only"]:
                raise SkillValidationError("内置 Skill 不能停用")
            safe_slug = _slug(slug)
            current = self.user_root / safe_slug if enabled is False else self.disabled_root / safe_slug
            target = self.disabled_root / safe_slug if enabled is False else self.user_root / safe_slug
            if target.exists() and target != current:
                raise SkillValidationError("Skill 目标状态目录已存在")
            if current.exists():
                shutil.move(str(current), str(target))
            return self.get(slug)

    def delete(self, slug: str) -> None:
        with self._mutation_lock:
            existing = self.get(slug)
            if existing["read_only"]:
                raise SkillValidationError("内置 Skill 不能删除")
            shutil.rmtree(Path(existing["path"]).parent)
