from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CATEGORY_HINTS: dict[str, tuple[str, ...]] = {
    "除吹雪机": ("除雪", "吹雪", "除霜", "热能", "雪雾", "车窗"),
    "吹风机": ("吹风", "风炮", "大风力", "落叶", "劈水", "池塘"),
}


@dataclass(frozen=True)
class ReferenceDoc:
    category: str
    relative_path: str
    absolute_path: Path
    filename: str
    title: str


class ReferenceLibrary:
    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self.root = self.project_root / "skills" / "video-references" / "references"

    def list_docs(self) -> list[ReferenceDoc]:
        docs: list[ReferenceDoc] = []
        if not self.root.is_dir():
            return docs
        for category_dir in sorted(path for path in self.root.iterdir() if path.is_dir()):
            for path in sorted(category_dir.glob("*.md")):
                rel = path.relative_to(self.project_root).as_posix()
                docs.append(
                    ReferenceDoc(
                        category=category_dir.name,
                        relative_path=rel,
                        absolute_path=path,
                        filename=path.name,
                        title=path.stem,
                    )
                )
        return docs

    def retrieve(self, brief: str, limit_same: int = 3, limit_cross: int = 1) -> list[dict[str, Any]]:
        docs = self.list_docs()
        if not docs:
            raise ValueError("video-references 参考库为空")
        brief_text = brief.strip() or "短视频创作"
        category = self._guess_category(brief_text, docs)
        same = [doc for doc in docs if doc.category == category]
        other_docs = [doc for doc in docs if doc.category != category]
        selected = self._rank(brief_text, same)[:limit_same]

        # A small category may not have three same-category examples. Fill the
        # primary slots with the best remaining documents without duplicates.
        if len(selected) < limit_same:
            for doc in self._rank(brief_text, other_docs):
                if doc not in selected:
                    selected.append(doc)
                if len(selected) >= limit_same:
                    break

        cross = [doc for doc in self._rank(brief_text, other_docs) if doc not in selected][:limit_cross]
        selected.extend(cross)
        results: list[dict[str, Any]] = []
        for doc in selected:
            excerpt = read_intent_excerpt(doc.absolute_path)
            why = "同品类结构参考" if doc.category == category else "跨品类借结构"
            if excerpt.get("skeleton"):
                why = f"{why}：{excerpt['skeleton'][:80]}"
            results.append(
                {
                    "path": doc.relative_path,
                    "why": why,
                    "category": doc.category,
                    "excerpt": excerpt,
                }
            )
        return results

    def read_full(self, relative_path: str) -> str:
        path = self._safe(relative_path)
        return path.read_text(encoding="utf-8")

    def _safe(self, relative_path: str) -> Path:
        path = (self.project_root / relative_path).resolve()
        if self.root not in path.parents and path != self.root:
            raise ValueError("参考路径越界")
        if not path.is_file():
            raise ValueError(f"参考文件不存在: {relative_path}")
        return path

    def _guess_category(self, brief: str, docs: list[ReferenceDoc]) -> str:
        text = brief.lower()
        scores: dict[str, int] = {}
        for category, hints in CATEGORY_HINTS.items():
            score = 0
            if category in brief:
                score += 8
            for hint in hints:
                if hint.lower() in text:
                    score += 3
            scores[category] = score
        # also score by filename tokens appearing in brief
        for doc in docs:
            hits = sum(1 for token in _tokens(doc.filename) if token in text or token in brief)
            scores[doc.category] = scores.get(doc.category, 0) + hits
        if scores and max(scores.values()) > 0:
            return max(scores, key=lambda key: scores[key])
        # Fallback: first category that actually has files.
        return docs[0].category

    def _rank(self, brief: str, docs: list[ReferenceDoc]) -> list[ReferenceDoc]:
        text = brief.lower()
        brief_tokens = set(_tokens(brief))

        def score(doc: ReferenceDoc) -> tuple[int, str]:
            name = doc.filename.lower()
            hits = sum(2 for token in brief_tokens if token and token in name)
            for token in brief_tokens:
                if token and token in doc.title:
                    hits += 1
            if any(token in text for token in _tokens(doc.title) if len(token) >= 2):
                hits += 1
            return (hits, doc.filename)

        return sorted(docs, key=score, reverse=True)


def _tokens(text: str) -> list[str]:
    parts = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{1,8}", text.lower())
    return [part for part in parts if len(part) >= 2]


def read_intent_excerpt(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    skeleton = lines[0].strip() if lines else ""
    notes = ""
    # First line is skeleton; notes live between the first and second '---'
    if "\n---\n" in text:
        after_first = text.split("\n---\n", 1)[1]
        notes = after_first.split("\n---", 1)[0].strip()
    return {"skeleton": skeleton, "notes": notes}
