from pathlib import Path

from starter_agent.settings import AgentSettings, ProviderConfig, load_settings


def test_provider_api_key_falls_back_to_project_env(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("TEST_PROVIDER_KEY", raising=False)
    (tmp_path / ".env").write_text(
        "TEST_PROVIDER_KEY=local-secret\n",
        encoding="utf-8",
    )
    settings = AgentSettings(
        providers={
            "test": ProviderConfig(
                type="openai_compatible",
                base_url="https://example.test/v1",
                api_key_env="TEST_PROVIDER_KEY",
            )
        },
        project_root=tmp_path,
    )

    assert settings.provider_api_key("test") == "local-secret"


def test_official_openai_provider_uses_native_model_ids() -> None:
    config_path = Path(__file__).resolve().parents[2] / "config" / "config.yaml"

    settings = load_settings(config_path)

    assert settings.providers["openai"].models == [
        "gpt-5.5",
        "gpt-5.6-terra",
    ]


def test_unconfigured_local_provider_is_not_exposed() -> None:
    config_path = Path(__file__).resolve().parents[2] / "config" / "config.yaml"

    settings = load_settings(config_path)

    assert "local" not in settings.providers
