from functools import lru_cache

from starter_agent.agent.context import ContextBuilder
from starter_agent.agent.runtime import AgentRuntime
from starter_agent.application import ApplicationService
from starter_agent.infrastructure.session_store import SQLiteSessionStore
from starter_agent.knowledge.service import KnowledgeApplicationService
from starter_agent.knowledge.store import SQLiteKnowledgeStore
from starter_agent.observability.logging import configure_logging
from starter_agent.providers.registry import ProviderRegistry
from starter_agent.settings import AgentSettings, load_settings
from starter_agent.tools.policy import ToolPolicy
from starter_agent.tools.registry import ToolRegistry


@lru_cache
def get_settings() -> AgentSettings:
    return load_settings()


@lru_cache
def create_application() -> ApplicationService:
    settings = get_settings()
    configure_logging(settings.resolve_path(settings.app.log_path))
    store = SQLiteSessionStore(settings.app.database_url, settings.project_root)
    providers = ProviderRegistry(settings)
    tools = ToolRegistry(settings.tools.enabled, settings=settings)
    policy = ToolPolicy(settings.tools.allow_risk_levels)
    runtime = AgentRuntime(tools, policy, settings.runtime, settings.context)
    context = ContextBuilder(
        settings.resolve_path(settings.app.identity_path),
        settings.project_root / "config/prompts/system.md",
    )
    return ApplicationService(
        settings=settings,
        store=store,
        providers=providers,
        runtime=runtime,
        context=context,
    )


@lru_cache
def create_knowledge_service() -> KnowledgeApplicationService:
    settings = get_settings()
    store = SQLiteKnowledgeStore(settings.app.database_url, settings.project_root)
    return KnowledgeApplicationService(settings, store)
