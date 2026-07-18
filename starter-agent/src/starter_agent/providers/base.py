from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

from starter_agent.domain.models import Message, ModelResponse


class Provider(ABC):
    name: str

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        model: str,
        tools: list[dict[str, Any]],
        on_delta: Callable[[str], Awaitable[None]] | None = None,
        tool_choice: str | None = None,
    ) -> ModelResponse:
        raise NotImplementedError

    @abstractmethod
    async def health(self, model: str) -> tuple[bool, str]:
        raise NotImplementedError
