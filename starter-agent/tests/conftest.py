from pathlib import Path

import pytest

from starter_agent.agent.context import ContextBuilder
from starter_agent.agent.runtime import AgentRuntime
from starter_agent.application import ApplicationService
from starter_agent.infrastructure.session_store import SQLiteSessionStore
from starter_agent.providers.registry import ProviderRegistry
from starter_agent.settings import AgentSettings, load_settings
from starter_agent.tools.policy import ToolPolicy
from starter_agent.tools.registry import ToolRegistry


class ControlledMcpManager:
    def __init__(self) -> None:
        self.start_count = 0
        self.shutdown_count = 0

    async def start(self) -> dict[str, object]:
        self.start_count += 1
        return {}

    async def shutdown(self) -> dict[str, str]:
        self.shutdown_count += 1
        return {}


@pytest.fixture(autouse=True)
def mcp_test_manager(monkeypatch) -> ControlledMcpManager:
    from starter_agent.interfaces import api as api_module

    manager = ControlledMcpManager()
    monkeypatch.setattr(api_module, "create_mcp_manager", lambda: manager)
    return manager


@pytest.fixture
def settings(tmp_path: Path) -> AgentSettings:
    loaded = load_settings("config/config.example.yaml")
    loaded.providers["mock"].models = ["starter-mock"]
    loaded.providers["zhipu"].models = ["glm-4.7", "glm-5.1"]
    loaded.project_root = tmp_path
    loaded.app.database_url = "sqlite:///agent.db"
    identity = tmp_path / "agent.md"
    identity.write_text("# Identity\nTest Agent", encoding="utf-8")
    prompt = tmp_path / "system.md"
    prompt.write_text("Identity:\n{identity}", encoding="utf-8")
    loaded.app.identity_path = "agent.md"
    return loaded


@pytest.fixture
def application(settings: AgentSettings, tmp_path: Path) -> ApplicationService:
    store = SQLiteSessionStore(settings.app.database_url, settings.project_root)
    providers = ProviderRegistry(settings)
    tools = ToolRegistry(settings.tools.enabled)
    runtime = AgentRuntime(
        tools,
        ToolPolicy(settings.tools.allow_risk_levels),
        settings.runtime,
    )
    context = ContextBuilder(tmp_path / "agent.md", tmp_path / "system.md")
    return ApplicationService(settings, store, providers, runtime, context)
