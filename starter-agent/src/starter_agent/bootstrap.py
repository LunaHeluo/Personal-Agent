from functools import lru_cache

from starter_agent.agent.context import ContextBuilder
from starter_agent.agent.runtime import AgentRuntime
from starter_agent.application import ApplicationService
from starter_agent.capabilities.store import CapabilityStore
from starter_agent.capabilities.registry import UnifiedToolRegistry
from starter_agent.capabilities.gate import PreToolCallGate, UnifiedToolExecutor
from starter_agent.capabilities.confirmations import (
    ConfirmationBroker,
    ConfirmationService,
    TurnCoordinator,
)
from starter_agent.infrastructure.session_store import SQLiteSessionStore
from starter_agent.knowledge.service import KnowledgeApplicationService
from starter_agent.knowledge.store import SQLiteKnowledgeStore
from starter_agent.mcp.config import McpConfigLoader
from starter_agent.mcp.manager import McpManager
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
    builtin_tools = ToolRegistry(settings.tools.enabled, settings=settings)
    tools = UnifiedToolRegistry(
        builtin_tools,
        allowed_risk_levels=settings.tools.allow_risk_levels,
    )
    policy = ToolPolicy(settings.tools.allow_risk_levels)
    capability_store = CapabilityStore(
        settings.app.database_url,
        settings.project_root,
    )
    gate = PreToolCallGate(capability_store, registry=tools)
    executor = UnifiedToolExecutor(capability_store, gate=gate)
    confirmation_service = ConfirmationService(
        capability_store,
        gate,
        broker=ConfirmationBroker(),
        confirmation_ttl_seconds=settings.mcp.confirmation_timeout_seconds,
        expire_orphans=True,
    )
    turn_coordinator = TurnCoordinator(
        confirmation_service,
        confirmation_timeout_seconds=settings.mcp.confirmation_timeout_seconds,
    )
    runtime = AgentRuntime(
        tools,
        policy,
        settings.runtime,
        settings.context,
        gate=gate,
        executor=executor,
        turn_coordinator=turn_coordinator,
    )
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


@lru_cache
def create_mcp_manager() -> McpManager:
    settings = get_settings()
    configuration = McpConfigLoader(settings.project_root).load(
        settings.mcp.config_path
    )
    store = CapabilityStore(settings.app.database_url, settings.project_root)
    return McpManager(
        configuration,
        store=store,
        tool_executor=create_application().runtime.executor,
        initialize_timeout_seconds=settings.mcp.initialize_timeout_seconds,
        shutdown_timeout_seconds=settings.mcp.shutdown_timeout_seconds,
    )
