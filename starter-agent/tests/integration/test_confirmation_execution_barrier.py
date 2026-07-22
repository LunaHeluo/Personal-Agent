from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from starter_agent.agent.runtime import AgentRuntime
from starter_agent.capabilities.confirmations import (
    ConfirmationBroker,
    ConfirmationService,
    TurnCoordinator,
)
from starter_agent.capabilities.gate import (
    PreToolCallGate,
    ToolExecutionDenied,
    UnifiedToolExecutor,
)
from starter_agent.capabilities.registry import UnifiedToolRegistry
from starter_agent.capabilities.store import CapabilityStore
from starter_agent.domain.models import ToolResult
from starter_agent.settings import RuntimeConfig
from starter_agent.tools.base import Tool
from starter_agent.tools.policy import ToolPolicy


class _CountingTool(Tool):
    name = "confirmation_test_tool"
    description = "A test-only governed tool"
    input_schema = {
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
        "additionalProperties": False,
    }
    risk_level = "write"

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, arguments, context):
        self.calls += 1
        return ToolResult(ok=True, data={"value": arguments["value"]}, display="done")


class _Registry:
    def __init__(self, tool):
        self.tools = {tool.name: tool}

    def get(self, name):
        return self.tools.get(name)

    def schemas(self):
        return [tool.schema() for tool in self.tools.values()]


def _runtime(tmp_path, *, timeout=1):
    tool = _CountingTool()
    source = _Registry(tool)
    registry = UnifiedToolRegistry(type("BuiltinView", (), {"list": lambda self: [tool], "email_manager": None})())
    store = CapabilityStore("sqlite:///:memory:", tmp_path)
    gate = PreToolCallGate(store, registry=registry)
    executor = UnifiedToolExecutor(store, gate=gate)
    broker = ConfirmationBroker()
    service = ConfirmationService(store, gate, broker=broker, confirmation_ttl_seconds=timeout)
    coordinator = TurnCoordinator(service, confirmation_timeout_seconds=timeout)
    runtime = AgentRuntime(
        source,
        ToolPolicy(["read", "write", "external", "dangerous"]),
        RuntimeConfig(),
        gate=gate,
        executor=executor,
        turn_coordinator=coordinator,
    )
    return runtime, service, tool


async def _pending(service):
    for _ in range(100):
        values = service.list_pending(session_id=None)
        if values:
            return values[-1]
        await asyncio.sleep(0.01)
    raise AssertionError("confirmation was not persisted")


async def test_runtime_waits_without_calling_tool_then_executes_once_after_approval(tmp_path) -> None:
    runtime, service, tool = _runtime(tmp_path)
    session_id, turn_id = uuid4(), uuid4()
    events = []

    async def on_event(event):
        events.append(event)

    task = asyncio.create_task(
        runtime.execute_tool(
            tool_name=tool.name,
            arguments={"value": 7},
            session_id=session_id,
            turn_id=turn_id,
            call_id="call-once",
            on_tool_event=on_event,
        )
    )
    pending = await _pending(service)
    assert tool.calls == 0
    assert events[-1]["type"] == "confirmation_required"

    service.decide(
        pending.id,
        expected_revision=pending.revision,
        idempotency_key="decision-once",
        decision="once",
    )
    result = await task
    assert result.ok is True
    assert tool.calls == 1

    with pytest.raises(ToolExecutionDenied, match="confirmation_consumed"):
        await runtime.execute_tool(
            tool_name=tool.name,
            arguments={"value": 7},
            session_id=session_id,
            turn_id=turn_id,
            call_id="call-once",
            confirmation_id=pending.id,
        )
    assert tool.calls == 1

    service.decide(
        pending.id,
        expected_revision=pending.revision,
        idempotency_key="decision-once",
        decision="once",
    )
    await asyncio.sleep(0)
    assert tool.calls == 1
    actions = [event.action for event in runtime.gate.store.list_audit_events()]
    assert "permit.consumed" in actions
    assert "tool.invoked" in actions


@pytest.mark.parametrize("decision", ["cancel", None])
async def test_cancel_and_timeout_never_enter_real_tool(tmp_path, decision) -> None:
    runtime, service, tool = _runtime(tmp_path, timeout=0.05)
    task = asyncio.create_task(
        runtime.execute_tool(
            tool_name=tool.name,
            arguments={"value": 3},
            session_id=uuid4(),
            turn_id=uuid4(),
            call_id=f"call-{decision or 'timeout'}",
        )
    )
    pending = await _pending(service)
    if decision:
        service.decide(
            pending.id,
            expected_revision=0,
            idempotency_key="decision-cancel",
            decision=decision,
        )
    with pytest.raises(ToolExecutionDenied):
        await task
    assert tool.calls == 0
