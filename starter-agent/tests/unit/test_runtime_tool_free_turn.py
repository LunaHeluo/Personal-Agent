from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

import pytest

from starter_agent.agent.runtime import AgentRuntime
from starter_agent.domain.errors import ToolsDisabledForTurnError
from starter_agent.domain.models import Message, ModelResponse, ToolCall
from starter_agent.providers.base import Provider
from starter_agent.settings import RuntimeConfig
from starter_agent.tools.policy import ToolPolicy
from starter_agent.tools.registry import ToolRegistry


class HallucinatedToolProvider(Provider):
    name = "hallucinated-tool"

    def __init__(self) -> None:
        self.requests: list[list[dict[str, Any]]] = []

    async def complete(
        self,
        messages: list[Message],
        model: str,
        tools: list[dict[str, Any]],
        on_delta: Callable[[str], Awaitable[None]] | None = None,
        tool_choice: str | None = None,
        context_revision: int | None = None,
    ) -> ModelResponse:
        del messages, on_delta, tool_choice, context_revision
        self.requests.append(tools)
        return ModelResponse(
            provider=self.name,
            model=model,
            tool_calls=[
                ToolCall(
                    id="forbidden-call",
                    name="get_current_time",
                    arguments={"timezone": "Asia/Shanghai"},
                )
            ],
        )

    async def health(self, model: str) -> tuple[bool, str]:
        return True, model


@pytest.mark.asyncio
async def test_tool_free_turn_rejects_hallucinated_tool_without_execution() -> None:
    provider = HallucinatedToolProvider()
    runtime = AgentRuntime(
        ToolRegistry(["get_current_time"]),
        ToolPolicy(["read"]),
        RuntimeConfig(max_model_calls=2),
    )

    with pytest.raises(ToolsDisabledForTurnError) as captured:
        await runtime.run(
            provider=provider,
            model="fixture-model",
            messages=[Message(role="user", content="你好")],
            session_id=uuid4(),
            turn_id=uuid4(),
            allow_tools=False,
        )

    assert captured.value.code == "tools_disabled_for_turn"
    assert provider.requests == [[]]


@pytest.mark.asyncio
async def test_tool_free_turn_cannot_require_a_tool() -> None:
    provider = HallucinatedToolProvider()
    runtime = AgentRuntime(
        ToolRegistry(["get_current_time"]),
        ToolPolicy(["read"]),
        RuntimeConfig(max_model_calls=1),
    )

    with pytest.raises(ToolsDisabledForTurnError):
        await runtime.run(
            provider=provider,
            model="fixture-model",
            messages=[Message(role="user", content="你好")],
            session_id=uuid4(),
            turn_id=uuid4(),
            required_tool_name="get_current_time",
            allow_tools=False,
        )

    assert provider.requests == []
