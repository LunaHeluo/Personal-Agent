from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from starter_agent.agent.runtime import AgentRuntime
from starter_agent.capabilities.models import (
    Server,
    Snapshot,
    Tool,
    canonical_json_sha256,
)
from starter_agent.capabilities.registry import UnifiedToolRegistry
from starter_agent.domain.models import Message, ModelResponse
from starter_agent.providers.base import Provider
from starter_agent.settings import RuntimeConfig
from starter_agent.tools.policy import ToolPolicy
from starter_agent.tools.registry import ToolRegistry


TOOL_NAME = "mcp__browser__read_page"
TOOL_DESCRIPTION = "Read a reviewed browser page"
TOOL_SCHEMA = {
    "type": "object",
    "properties": {"url": {"type": "string", "format": "uri"}},
    "required": ["url"],
    "additionalProperties": False,
}


class _RequestCapturingProvider(Provider):
    name = "request-capturing"

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def complete(
        self,
        messages: list[Message],
        model: str,
        tools: list[dict[str, Any]],
        on_delta: Callable[[str], Awaitable[None]] | None = None,
        tool_choice: str | None = None,
        *,
        context_revision: int | None = None,
    ) -> ModelResponse:
        del messages, on_delta, tool_choice
        self.requests.append(
            {
                "tools": tools,
                "context_revision": context_revision,
            }
        )
        return ModelResponse(content="done", provider=self.name, model=model)

    async def health(self, model: str) -> tuple[bool, str]:
        return True, model


def _server() -> Server:
    return Server(
        id="browser",
        name="browser",
        config_source="tests/mcp.json",
        config_hash="a" * 64,
        enabled=True,
        connection_state="ready",
    )


def _snapshot() -> Snapshot:
    return Snapshot(
        id="browser-snapshot-1",
        server_id="browser",
        version=1,
        schema_hash="b" * 64,
        discovered_at=datetime.now(UTC),
        active=True,
        tool_count=1,
    )


def _tool() -> Tool:
    return Tool(
        snapshot_id="browser-snapshot-1",
        server_id="browser",
        upstream_name="read_page",
        model_alias=TOOL_NAME,
        description=TOOL_DESCRIPTION,
        input_schema=TOOL_SCHEMA,
        schema_hash=canonical_json_sha256(TOOL_SCHEMA),
        enabled=True,
        review_state="approved",
        reviewed_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_provider_request_trace_removes_and_restores_complete_tool_definition() -> None:
    registry = UnifiedToolRegistry(ToolRegistry([]))
    registry.refresh_server(_server(), [_tool()], snapshot=_snapshot())
    provider = _RequestCapturingProvider()
    runtime = AgentRuntime(
        registry,
        ToolPolicy(["external"]),
        RuntimeConfig(max_model_calls=1),
    )

    async def request(content: str) -> None:
        await runtime.run(
            provider=provider,
            model="test-model",
            messages=[Message(role="user", content=content)],
            session_id=uuid4(),
            turn_id=uuid4(),
        )

    await request("enabled")
    registry.set_tool_enabled(TOOL_NAME, False)
    await request("disabled")
    registry.set_tool_enabled(TOOL_NAME, True)
    registry.set_tool_review(TOOL_NAME, "unreviewed")
    await request("unreviewed")
    # A trusted review carries review provenance; a bare UI state flip does not.
    registry.refresh_server(_server(), [_tool()], snapshot=_snapshot())
    await request("reviewed again")

    expected_definition = {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": TOOL_DESCRIPTION,
            "parameters": TOOL_SCHEMA,
        },
    }
    assert [request["tools"] for request in provider.requests] == [
        [expected_definition],
        [],
        [],
        [expected_definition],
    ]
    revisions = [request["context_revision"] for request in provider.requests]
    assert all(type(revision) is int for revision in revisions)
    assert revisions == sorted(revisions)
    assert len(set(revisions)) == len(revisions)

    disabled_wire_payload = repr(provider.requests[1]["tools"])
    for absent in (TOOL_NAME, TOOL_DESCRIPTION, repr(TOOL_SCHEMA)):
        assert absent not in disabled_wire_payload
