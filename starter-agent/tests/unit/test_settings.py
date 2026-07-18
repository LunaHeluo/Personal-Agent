from starter_agent.settings import AgentSettings, ProviderConfig


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
