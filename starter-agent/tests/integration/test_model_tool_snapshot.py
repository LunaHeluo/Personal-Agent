from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from starter_agent.agent.runtime import AgentRuntime
from starter_agent.capabilities.registry import UnifiedToolRegistry
from starter_agent.domain.models import Message, ModelResponse, ToolCall
from starter_agent.interfaces import api as api_module
from starter_agent.interfaces.api import create_api
from starter_agent.providers.base import Provider
from starter_agent.settings import RuntimeConfig
from starter_agent.tools.policy import ToolPolicy
from starter_agent.tools.registry import ToolRegistry


class _RecordingProvider(Provider):
    name = "recording"

    def __init__(self, registry: UnifiedToolRegistry) -> None:
        self.registry = registry
        self.requests: list[list[dict[str, Any]]] = []

    async def complete(
        self,
        messages: list[Message],
        model: str,
        tools: list[dict[str, Any]],
        on_delta: Callable[[str], Awaitable[None]] | None = None,
        tool_choice: str | None = None,
    ) -> ModelResponse:
        self.requests.append(tools)
        if len(self.requests) == 1:
            self.registry.set_tool_enabled("get_current_time", False)
            return ModelResponse(
                provider=self.name,
                model=model,
                tool_calls=[
                    ToolCall(id="call-1", name="get_current_time", arguments={})
                ],
            )
        return ModelResponse(content="done", provider=self.name, model=model)

    async def health(self, model: str) -> tuple[bool, str]:
        return True, model


@pytest.mark.asyncio
async def test_each_real_model_request_reads_one_fresh_snapshot() -> None:
    registry = UnifiedToolRegistry(ToolRegistry(["get_current_time"]))
    provider = _RecordingProvider(registry)
    runtime = AgentRuntime(
        registry,
        ToolPolicy(["read"]),
        RuntimeConfig(max_model_calls=2),
    )

    response, _, _ = await runtime.run(
        provider=provider,
        model="test-model",
        messages=[Message(role="user", content="time")],
        session_id=uuid4(),
        turn_id=uuid4(),
    )

    assert [tool["function"]["name"] for tool in provider.requests[0]] == [
        "get_current_time"
    ]
    assert provider.requests[1] == []
    assert response.context_revision == registry.context_revision


def test_tools_api_is_compatible_and_does_not_leak_schema(monkeypatch) -> None:
    registry = UnifiedToolRegistry(ToolRegistry(["get_current_time"]))
    registry.set_tool_enabled("get_current_time", False)

    class _Application:
        class _Runtime:
            tools = registry

        runtime = _Runtime()

        async def wait_for_background_tasks(self) -> None:
            return None

    class _Manager:
        async def start(self) -> dict[str, object]:
            return {}

        async def shutdown(self) -> dict[str, str]:
            return {}

    monkeypatch.setattr(api_module, "create_application", lambda: _Application())
    monkeypatch.setattr(api_module, "create_mcp_manager", lambda: _Manager())

    with TestClient(create_api()) as client:
        response = client.get("/v1/tools")

    assert response.status_code == 200
    tool = response.json()["tools"][0]
    assert tool["name"] == "get_current_time"
    assert tool["description"]
    assert tool["source"] == "builtin"
    assert tool["callable"] is False
    serialized = response.text.lower()
    assert "input_schema" not in serialized
    assert "parameters" not in serialized
    assert "properties" not in serialized
