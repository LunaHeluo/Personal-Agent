from __future__ import annotations

from starter_agent.domain.errors import (
    ProviderApiKeyMissingError,
    ProviderBaseUrlMissingError,
    ProviderNotConfiguredError,
)
from starter_agent.providers.base import Provider
from starter_agent.providers.mock import MockProvider
from starter_agent.providers.openai_compatible import OpenAICompatibleProvider
from starter_agent.settings import AgentSettings


class ProviderRegistry:
    def __init__(self, settings: AgentSettings):
        self.settings = settings

    def names(self) -> list[str]:
        return sorted(self.settings.providers)

    def get(self, name: str) -> Provider:
        if name not in self.settings.providers:
            raise ProviderNotConfiguredError(
                provider=name,
                suggestion=f"请重新选择模型服务，或在配置文件中添加 {name}",
            )
        config = self.settings.providers[name]
        if config.type == "mock":
            provider = MockProvider()
            provider.name = name
            return provider
        api_key = self.settings.provider_api_key(name)
        if not api_key:
            raise ProviderApiKeyMissingError(
                suggestion=(
                    f"请在环境变量或项目 .env 文件中配置 {config.api_key_env}，"
                    "然后重新启动服务"
                ),
                provider=name,
            )
        if not config.base_url:
            raise ProviderBaseUrlMissingError(provider=name)
        return OpenAICompatibleProvider(
            name=name,
            base_url=config.base_url,
            api_key=api_key,
            timeout=self.settings.model.timeout_seconds,
            max_retries=self.settings.model.max_retries,
            temperature=self.settings.model.temperature,
            stream=config.stream,
            thinking=config.thinking,
        )
