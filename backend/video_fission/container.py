from __future__ import annotations

from .config import Settings
from .config import settings as default_settings
from .db import Database
from .productions import ProductionService
from .production_agent import ProductionAgent
from .script_agent import VideoScriptAgent
from .service import CodexHub
from .settings_store import SettingsStore
from .skills import SkillManager
from .tools import build_registry

settings = default_settings
db: Database
skills: SkillManager
settings_store: SettingsStore
productions: ProductionService
tools: object
hub: CodexHub


def configure(runtime_settings: Settings | None = None) -> None:
    global settings, db, skills, settings_store, productions, tools, hub
    settings = runtime_settings or default_settings
    settings.ensure_directories()
    db = Database(settings.database_path)
    db.initialize()
    if settings.auth_required:
        from .auth import bootstrap_auth

        bootstrap_auth()
    skills = SkillManager(
        settings.skills_dir,
        settings.bundled_skills_dir,
        settings.disabled_skills_dir,
    )
    settings_store = SettingsStore(db, settings)
    productions = ProductionService(settings, db, settings_store)
    if not settings.allow_local_copy_fallback:
        productions.engine.script_agent = VideoScriptAgent(settings, db, skills)
    tools = build_registry(db, settings.workspace, settings.artifacts_dir, productions)
    productions.agent = ProductionAgent(
        settings,
        db,
        skills,
        tools,
        engine=productions.engine,
    )
    hub = CodexHub(settings, db, skills, tools, productions=productions)


configure()
