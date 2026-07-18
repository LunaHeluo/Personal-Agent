from pathlib import Path

from starter_agent.settings import (
    AgentSettings,
    ProviderConfig,
    SerpApiKeyConfig,
    SerpApiToolConfig,
    ToolsConfig,
)


def make_settings() -> AgentSettings:
    return AgentSettings(
        project_root=Path("D:/missing-serpapi-test-project"),
        providers={"mock": ProviderConfig(type="mock", models=["starter-mock"])},
        tools=ToolsConfig(
            serpapi=SerpApiToolConfig(
                active_key="primary",
                keys={
                    "primary": SerpApiKeyConfig(api_key_env="SERPAPI_API_KEY"),
                    "backup": SerpApiKeyConfig(
                        api_key_env="SERPAPI_API_KEY_BACKUP"
                    ),
                },
            )
        ),
    )


def test_primary_profile_is_default(monkeypatch) -> None:
    settings = make_settings()
    monkeypatch.delenv("SERPAPI_ACTIVE_KEY", raising=False)
    monkeypatch.setenv("SERPAPI_API_KEY", "primary-secret")

    assert settings.serpapi_api_key() == (
        "primary",
        "primary-secret",
        "SERPAPI_API_KEY",
    )


def test_active_profile_can_switch_to_backup(monkeypatch) -> None:
    settings = make_settings()
    monkeypatch.setenv("SERPAPI_ACTIVE_KEY", "backup")
    monkeypatch.setenv("SERPAPI_API_KEY_BACKUP", "backup-secret")

    assert settings.serpapi_api_key() == (
        "backup",
        "backup-secret",
        "SERPAPI_API_KEY_BACKUP",
    )


def test_unknown_profile_does_not_fall_back(monkeypatch) -> None:
    settings = make_settings()
    monkeypatch.setenv("SERPAPI_ACTIVE_KEY", "missing")
    monkeypatch.setenv("SERPAPI_API_KEY", "must-not-be-used")

    assert settings.serpapi_api_key() == ("missing", None, None)
