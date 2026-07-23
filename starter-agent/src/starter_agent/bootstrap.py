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
from starter_agent.skills.registry import SkillRegistry
from starter_agent.skills.job_research import JobResearchOrchestrator
from starter_agent.skills.selector import SkillSelector
from starter_agent.tools.builtin.knowledge import RetrieveResumeEvidenceTool
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
    knowledge = KnowledgeApplicationService(
        settings,
        SQLiteKnowledgeStore(
            settings.app.database_url, settings.project_root
        ),
    )
    enabled_tools = list(settings.tools.enabled)
    if RetrieveResumeEvidenceTool.name not in enabled_tools:
        enabled_tools.append(RetrieveResumeEvidenceTool.name)
    builtin_tools = ToolRegistry(
        enabled_tools,
        settings=settings,
        knowledge_service=knowledge,
    )
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
        knowledge_scope=knowledge.scope,
        knowledge_base_id=knowledge.default_knowledge_base_id,
    )
    skills = SkillRegistry(
        settings.project_root / "skills",
        store=capability_store,
        dependency_resolver=lambda dependency: (
            (
                (capability := tools.resolve_execution(dependency.name))
                is not None
                and capability.enabled
                and capability.connected
                and capability.review_state == "approved"
                and executor.has_invoker(
                    capability.server_id,
                    capability.canonical_name,
                )
            )
            if dependency.kind in {"tool", "mcp"}
            else dependency.name == "job_description_ingestion"
        ),
    )
    skills.reload()
    context = ContextBuilder(
        settings.resolve_path(settings.app.identity_path),
        settings.project_root / "config/prompts/system.md",
        skill_registry=skills,
        skill_selector=SkillSelector(skills),
    )
    application = ApplicationService(
        settings=settings,
        store=store,
        providers=providers,
        runtime=runtime,
        context=context,
    )
    application.configure_job_description_ingestion(knowledge)
    application.configure_job_research(
        JobResearchOrchestrator(
            tools,
            executor,
            ingestion_available=lambda: (
                application.job_description_ingestion is not None
            ),
        )
    )
    return application


@lru_cache
def create_knowledge_service() -> KnowledgeApplicationService:
    service = create_application().job_description_ingestion
    assert service is not None
    return service.knowledge


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
