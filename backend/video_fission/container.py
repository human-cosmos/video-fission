from __future__ import annotations

from .config import settings
from .db import Database
from .service import CodexHub
from .skills import SkillManager
from .tools import build_registry

settings.ensure_directories()
db = Database(settings.database_path)
skills = SkillManager(
    settings.skills_dir,
    settings.bundled_skills_dir,
    settings.disabled_skills_dir,
)
tools = build_registry(db, settings.workspace, settings.artifacts_dir)
hub = CodexHub(settings, db, skills, tools)
