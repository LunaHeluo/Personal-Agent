from pathlib import Path

import pytest

from starter_agent.domain.errors import (
    ProviderApiKeyMissingError,
    ProviderNotConfiguredError,
)
from starter_agent.providers.registry import ProviderRegistry
from starter_agent.settings import AgentSettings, ProviderConfig


def test_missing_api_key_has_actionable_chinese_message(monkeypatch) -> None:
    settings = AgentSettings(
        project_root=Path("D:/definitely-missing-starter-agent-test-root"),
        providers={
            "tokenrouter": ProviderConfig(
                type="openai_compatible",
                base_url="https://example.test/v1",
                api_key_env="TOKENROUTER_API_KEY",
            )
        }
    )
    provider_name = "tokenrouter"
    config = settings.providers[provider_name]
    monkeypatch.delenv(config.api_key_env, raising=False)

    with pytest.raises(ProviderApiKeyMissingError) as caught:
        ProviderRegistry(settings).get(provider_name)

    payload = caught.value.to_public_dict()
    assert payload["message"] == "当前模型服务尚未配置 API Key"
    assert config.api_key_env in payload["suggestion"]
    assert "重新启动服务" in payload["suggestion"]


def test_unknown_provider_has_actionable_chinese_message() -> None:
    settings = AgentSettings(providers={"mock": ProviderConfig(type="mock")})
    with pytest.raises(ProviderNotConfiguredError) as caught:
        ProviderRegistry(settings).get("missing-provider")

    assert str(caught.value) == "所选模型服务尚未配置"
    assert "missing-provider" in caught.value.suggestion
