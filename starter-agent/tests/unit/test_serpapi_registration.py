from starter_agent.providers.registry import ProviderRegistry
from starter_agent.tools.registry import ToolRegistry


def test_serpapi_tool_is_registered_when_enabled(settings) -> None:
    settings.tools.enabled = ["search_jobs_serpapi"]
    registry = ToolRegistry(settings.tools.enabled, settings=settings)

    assert registry.get("search_jobs_serpapi") is not None
    assert [schema["function"]["name"] for schema in registry.schemas()] == [
        "search_jobs_serpapi"
    ]


def test_serpapi_tool_is_absent_when_disabled(settings) -> None:
    registry = ToolRegistry([], settings=settings)
    assert registry.get("search_jobs_serpapi") is None


def test_provider_registry_import_remains_available() -> None:
    assert ProviderRegistry is not None
