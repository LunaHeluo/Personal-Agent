from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from starter_agent.agent.runtime import AgentRuntime
from starter_agent.capabilities.models import Server, Tool, canonical_json_sha256
from starter_agent.capabilities.registry import UnifiedToolRegistry
from starter_agent.domain.models import Message, ModelResponse
from starter_agent.interfaces import api as api_module
from starter_agent.interfaces.api import create_api
from starter_agent.providers.base import Provider
from starter_agent.settings import RuntimeConfig
from starter_agent.tools.policy import ToolPolicy
from starter_agent.tools.registry import ToolRegistry


MCP_NAME = "mcp__browser__read_page"
MCP_DESCRIPTION = "Read a complete browser page using the reviewed MCP schema"
MCP_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"url": {"type": "string", "format": "uri"}},
    "required": ["url"],
    "additionalProperties": False,
}


def _server() -> Server:
    return Server(
        id="browser",
        name="browser",
        config_source="tests/mcp.json",
        config_hash="a" * 64,
        enabled=True,
        connection_state="ready",
    )


def _mcp_tool() -> Tool:
    return Tool(
        snapshot_id="browser-snapshot-1",
        server_id="browser",
        upstream_name="read_page",
        model_alias=MCP_NAME,
        description=MCP_DESCRIPTION,
        input_schema=MCP_INPUT_SCHEMA,
        schema_hash=canonical_json_sha256(MCP_INPUT_SCHEMA),
        enabled=True,
        review_state="approved",
    )


class _RecordingProvider(Provider):
    name = "recording"

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.requests: list[list[dict[str, Any]]] = []

    async def complete(
        self,
        messages: list[Message],
        model: str,
        tools: list[dict[str, Any]],
        on_delta: Callable[[str], Awaitable[None]] | None = None,
        tool_choice: str | None = None,
    ) -> ModelResponse:
        self.events.append("provider.complete")
        self.requests.append(tools)
        return ModelResponse(content="done", provider=self.name, model=model)

    async def health(self, model: str) -> tuple[bool, str]:
        return True, model


@pytest.mark.asyncio
async def test_dynamic_mcp_definition_tracks_every_state_on_real_model_requests(
    monkeypatch,
) -> None:
    registry = UnifiedToolRegistry(ToolRegistry([]))
    registry.refresh_server(_server(), [_mcp_tool()])
    events: list[str] = []
    original_model_snapshot = registry.model_snapshot

    def read_snapshot():
        events.append("registry.model_snapshot")
        return original_model_snapshot()

    monkeypatch.setattr(registry, "model_snapshot", read_snapshot)
    provider = _RecordingProvider(events)
    runtime = AgentRuntime(
        registry,
        ToolPolicy(["external"]),
        RuntimeConfig(max_model_calls=1),
    )

    async def request() -> None:
        await runtime.run(
            provider=provider,
            model="test-model",
            messages=[Message(role="user", content="read")],
            session_id=uuid4(),
            turn_id=uuid4(),
        )

    await request()
    registry.set_server_connected("browser", False)
    await request()
    registry.set_server_connected("browser", True)
    registry.set_server_enabled("browser", False)
    await request()
    registry.set_server_enabled("browser", True)
    registry.set_tool_enabled(MCP_NAME, False)
    await request()
    registry.set_tool_enabled(MCP_NAME, True)
    registry.set_tool_review(MCP_NAME, "unreviewed")
    await request()
    registry.set_tool_review(MCP_NAME, "approved")
    await request()

    assert provider.requests[0] == [
        {
            "type": "function",
            "function": {
                "name": MCP_NAME,
                "description": MCP_DESCRIPTION,
                "parameters": MCP_INPUT_SCHEMA,
            },
        }
    ]
    for request_tools in provider.requests[1:5]:
        assert request_tools == []
    assert provider.requests[5] == provider.requests[0]
    assert events == [
        item
        for _ in provider.requests
        for item in ("registry.model_snapshot", "provider.complete")
    ]


def test_tools_api_is_compatible_and_does_not_leak_schema(monkeypatch) -> None:
    registry = UnifiedToolRegistry(ToolRegistry(["get_current_time"]))
    registry.refresh_server(_server(), [_mcp_tool()])
    registry.set_tool_enabled(MCP_NAME, False)

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
    tools = {tool["name"]: tool for tool in response.json()["tools"]}
    assert tools["get_current_time"]["description"]
    tool = tools[MCP_NAME]
    assert tool == {
        "name": MCP_NAME,
        "source": "mcp",
        "server": "browser",
        "type": "mcp",
        "enabled": False,
        "review": "approved",
        "callable": False,
    }
    serialized = response.text.lower()
    assert MCP_DESCRIPTION.lower() not in serialized
    assert "description" not in tool
    assert "input_schema" not in serialized
    assert "output_schema" not in serialized
    assert "parameters" not in serialized
    assert "properties" not in serialized
