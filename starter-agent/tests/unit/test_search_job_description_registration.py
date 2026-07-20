import pytest
from pydantic import ValidationError

from starter_agent.settings import JobDescriptionToolConfig, load_settings
from starter_agent.tools.registry import ToolRegistry


def test_job_description_config_has_safe_defaults() -> None:
    config = JobDescriptionToolConfig()

    assert config.fetch_timeout_seconds == 10
    assert config.max_response_bytes == 1_000_000
    assert config.max_redirects == 3
    assert config.user_agent == "StarterAgentJobDescription/0.1"
    assert config.respect_robots is True


def test_runtime_config_enables_job_description_tool() -> None:
    settings = load_settings("config/config.yaml")

    assert "search_job_description" in settings.tools.enabled
    assert settings.tools.job_description.max_response_bytes == 1_000_000
    ToolRegistry(settings.tools.enabled, settings=settings)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("fetch_timeout_seconds", 0),
        ("fetch_timeout_seconds", -1),
        ("fetch_timeout_seconds", 30.1),
        ("max_response_bytes", 9_999),
        ("max_response_bytes", 5_000_001),
        ("max_redirects", -1),
        ("max_redirects", 6),
    ],
)
def test_job_description_config_rejects_out_of_range_budgets(
    field: str, value: int | float
) -> None:
    with pytest.raises(ValidationError):
        JobDescriptionToolConfig(**{field: value})


@pytest.mark.parametrize(
    "user_agent",
    [
        "",
        "   ",
        "Starter\nAgent",
        "Starter\rAgent",
        "Starter\x00Agent",
        "Starter\x1fAgent",
        "招聘助手/1.0",
        "Agenté/1.0",
        "Agent🤖/1.0",
        "x" * 201,
    ],
)
def test_job_description_config_rejects_unsafe_user_agent(user_agent: str) -> None:
    with pytest.raises(ValidationError):
        JobDescriptionToolConfig(user_agent=user_agent)


def test_job_description_config_trims_user_agent() -> None:
    config = JobDescriptionToolConfig(user_agent="  Starter Agent/1.0  ")

    assert config.user_agent == "Starter Agent/1.0"


def test_job_description_tool_is_registered_when_enabled() -> None:
    settings = load_settings("config/config.example.yaml")
    settings.tools.enabled = ["search_job_description"]

    registry = ToolRegistry(settings.tools.enabled, settings=settings)

    tool = registry.get("search_job_description")
    assert tool is not None
    assert tool.risk_level == "read"
