from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

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
from starter_agent.capabilities.models import Confirmation
from starter_agent.capabilities.registry import UnifiedToolRegistry
from starter_agent.capabilities.store import CapabilityStore
from starter_agent.domain.models import Message, ModelResponse, ToolCall, ToolResult
from starter_agent.providers.base import Provider
from starter_agent.interfaces.capabilities_api import (
    CapabilityApiServices,
    ManagementPrincipal,
    create_capabilities_router,
    get_capability_services,
    get_management_principal,
)
from starter_agent.settings import RuntimeConfig
from starter_agent.tools.base import Tool
from starter_agent.tools.policy import ToolPolicy


class _CountingTool(Tool):
    name = "chat_confirmation_test_tool"
    description = "A governed test tool"
    input_schema = {
        "type": "object",
        "properties": {"target": {"type": "string"}},
        "required": ["target"],
        "additionalProperties": False,
    }
    risk_level = "write"

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, arguments, context):
        self.calls += 1
        return ToolResult(
            ok=True,
            data={"target": arguments["target"]},
            display="executed once",
        )


class _ToolSource:
    def __init__(self, tool: Tool) -> None:
        self.tools = {tool.name: tool}

    def get(self, name):
        return self.tools.get(name)

    def schemas(self):
        return [tool.schema() for tool in self.tools.values()]


class _FlowProvider(Provider):
    name = "confirmation-flow"

    async def complete(
        self,
        messages,
        model,
        tools,
        on_delta=None,
        tool_choice=None,
    ):
        if messages[-1].role == "tool":
            return ModelResponse(content="done", provider=self.name, model=model)
        return ModelResponse(
            provider=self.name,
            model=model,
            tool_calls=[
                ToolCall(
                    id="call-runtime-flow",
                    name="chat_confirmation_test_tool",
                    arguments={"target": "local"},
                )
            ],
        )

    async def health(self, model: str) -> tuple[bool, str]:
        return True, model


def _runtime(tmp_path, *, timeout: float = 1):
    tool = _CountingTool()
    builtin_view = type(
        "BuiltinView",
        (),
        {"list": lambda self: [tool], "email_manager": None},
    )()
    registry = UnifiedToolRegistry(builtin_view)
    store = CapabilityStore("sqlite:///:memory:", tmp_path)
    gate = PreToolCallGate(store, registry=registry)
    executor = UnifiedToolExecutor(store, gate=gate)
    broker = ConfirmationBroker()
    confirmations = ConfirmationService(
        store,
        gate,
        broker=broker,
        confirmation_ttl_seconds=timeout,
    )
    runtime = AgentRuntime(
        _ToolSource(tool),
        ToolPolicy(["read", "write", "external", "dangerous"]),
        RuntimeConfig(),
        gate=gate,
        executor=executor,
        turn_coordinator=TurnCoordinator(
            confirmations,
            confirmation_timeout_seconds=timeout,
        ),
    )
    return runtime, confirmations, tool


async def _wait_for_pending(
    confirmations: ConfirmationService,
) -> Confirmation:
    for _ in range(100):
        pending = confirmations.list_pending(session_id=None)
        if pending:
            return pending[-1]
        await asyncio.sleep(0.01)
    raise AssertionError("confirmation was not persisted")


async def test_confirmation_stream_events_execute_once_only_after_current_decision(
    tmp_path,
) -> None:
    runtime, confirmations, tool = _runtime(tmp_path)
    session_id, turn_id = uuid4(), uuid4()
    events: list[dict] = []

    async def on_event(event: dict) -> None:
        events.append(event)

    task = asyncio.create_task(
        runtime.execute_tool(
            tool_name=tool.name,
            arguments={"target": "https://jobs.example.com/opening"},
            session_id=session_id,
            turn_id=turn_id,
            call_id="call-chat-once",
            principal="local-user",
            on_tool_event=on_event,
        )
    )
    pending = await _wait_for_pending(confirmations)

    assert tool.calls == 0
    required = events[-1]
    assert required["type"] == "confirmation_required"
    assert required["principal"] == "local-user"
    assert required["session_id"] == str(session_id)
    assert required["turn_id"] == str(turn_id)
    assert required["call_id"] == "call-chat-once"
    assert required["audit_ref"].startswith("audit-")
    assert required["trace_ref"]
    assert required["tool_invoked"] is False

    confirmations.decide(
        pending.id,
        expected_revision=pending.revision,
        idempotency_key="stable-chat-once-key",
        decision="once",
        actor="local-user",
    )
    result = await task

    assert result.ok is True
    assert tool.calls == 1
    assert [event["type"] for event in events] == [
        "confirmation_required",
        "confirmation_resolved",
        "tool_started",
    ]
    assert events[1]["status"] == "approved"
    assert events[1]["gate_revalidated"] is True
    assert events[2]["audit_ref"].startswith("audit-")

    confirmations.decide(
        pending.id,
        expected_revision=pending.revision,
        idempotency_key="stable-chat-once-key",
        decision="once",
        actor="local-user",
    )
    assert tool.calls == 1


async def test_runtime_run_emits_one_guarded_terminal_event_per_tool_call(
    tmp_path,
) -> None:
    runtime, confirmations, tool = _runtime(tmp_path)
    session_id, turn_id = uuid4(), uuid4()
    events: list[dict] = []
    artifacts: list[dict] = []

    async def on_event(event: dict) -> None:
        events.append(event)

    async def on_artifact(artifact: dict) -> None:
        artifacts.append(artifact)

    task = asyncio.create_task(
        runtime.run(
            provider=_FlowProvider(),
            model="fixture",
            messages=[Message(role="user", content="run it")],
            session_id=session_id,
            turn_id=turn_id,
            required_tool_name=tool.name,
            on_tool_event=on_event,
            on_tool_artifact=on_artifact,
        )
    )
    pending = await _wait_for_pending(confirmations)
    assert tool.calls == 0
    confirmations.decide(
        pending.id,
        expected_revision=pending.revision,
        idempotency_key="stable-runtime-run-once-key",
        decision="once",
        actor="local-user",
    )

    await task

    flow_events = [
        event
        for event in events
        if event.get("call_id") == "call-runtime-flow"
    ]
    assert [event["type"] for event in flow_events] == [
        "confirmation_required",
        "confirmation_resolved",
        "tool_started",
        "tool_completed",
    ]
    completed = [
        event for event in flow_events if event["type"] == "tool_completed"
    ]
    assert len(completed) == 1
    assert completed[0]["tool_invoked"] is True
    assert completed[0]["raw_source_ref"] == artifacts[0]["source_ref"]
    assert completed[0]["content_sha256"] == artifacts[0]["content_sha256"]
    assert completed[0]["audit_ref"].startswith("audit-")
    assert completed[0]["trace_ref"] == (
        f"trace:{session_id}:{turn_id}:call-runtime-flow"
    )


@pytest.mark.parametrize(
    ("decision", "terminal_status"),
    [("cancel", "cancelled"), (None, "expired")],
)
async def test_runtime_run_cancel_and_timeout_each_emit_one_terminal(
    tmp_path,
    decision,
    terminal_status,
) -> None:
    runtime, confirmations, tool = _runtime(tmp_path, timeout=0.05)
    events: list[dict] = []

    async def on_event(event: dict) -> None:
        events.append(event)

    task = asyncio.create_task(
        runtime.run(
            provider=_FlowProvider(),
            model="fixture",
            messages=[Message(role="user", content="run it")],
            session_id=uuid4(),
            turn_id=uuid4(),
            required_tool_name=tool.name,
            on_tool_event=on_event,
        )
    )
    if decision is not None:
        pending = await _wait_for_pending(confirmations)
        confirmations.decide(
            pending.id,
            expected_revision=pending.revision,
            idempotency_key="stable-runtime-terminal-key",
            decision=decision,
            actor="local-user",
        )

    await task

    flow_events = [
        event
        for event in events
        if event.get("call_id") == "call-runtime-flow"
    ]
    assert sum(event["type"] == "tool_completed" for event in flow_events) == 1
    assert not any(event["type"] == "tool_started" for event in flow_events)
    completed = [
        event for event in flow_events if event["type"] == "tool_completed"
    ][0]
    assert any(event["type"] == "confirmation_required" for event in flow_events)
    assert completed["status"] == terminal_status
    assert completed["tool_invoked"] is False
    assert tool.calls == 0


async def test_runtime_run_failure_emits_one_invoked_terminal(
    tmp_path,
    monkeypatch,
) -> None:
    runtime, confirmations, tool = _runtime(tmp_path)
    events: list[dict] = []

    async def fail(_arguments, _context):
        tool.calls += 1
        raise RuntimeError("fixture failure")

    async def on_event(event: dict) -> None:
        events.append(event)

    monkeypatch.setattr(tool, "execute", fail)
    task = asyncio.create_task(
        runtime.run(
            provider=_FlowProvider(),
            model="fixture",
            messages=[Message(role="user", content="run it")],
            session_id=uuid4(),
            turn_id=uuid4(),
            required_tool_name=tool.name,
            on_tool_event=on_event,
        )
    )
    pending = await _wait_for_pending(confirmations)
    confirmations.decide(
        pending.id,
        expected_revision=pending.revision,
        idempotency_key="stable-runtime-failure-key",
        decision="once",
        actor="local-user",
    )

    await task

    completed = [
        event
        for event in events
        if event.get("call_id") == "call-runtime-flow"
        and event["type"] == "tool_completed"
    ]
    assert len(completed) == 1
    assert completed[0]["status"] == "failed"
    assert completed[0]["tool_invoked"] is True
    assert completed[0]["audit_ref"].startswith("audit-")
    assert tool.calls == 1


@pytest.mark.parametrize(
    ("decision", "terminal_status"),
    [("cancel", "cancelled"), (None, "expired")],
)
async def test_cancel_and_timeout_emit_authoritative_zero_invocation_terminal(
    tmp_path,
    decision,
    terminal_status,
) -> None:
    runtime, confirmations, tool = _runtime(tmp_path, timeout=0.05)
    events: list[dict] = []

    async def on_event(event: dict) -> None:
        events.append(event)

    task = asyncio.create_task(
        runtime.execute_tool(
            tool_name=tool.name,
            arguments={"target": "local"},
            session_id=uuid4(),
            turn_id=uuid4(),
            call_id=f"call-{terminal_status}",
            on_tool_event=on_event,
        )
    )
    if decision is not None:
        pending = await _wait_for_pending(confirmations)
        confirmations.decide(
            pending.id,
            expected_revision=pending.revision,
            idempotency_key="stable-chat-cancel-key",
            decision=decision,
            actor="local-user",
        )

    with pytest.raises(ToolExecutionDenied):
        await task

    assert tool.calls == 0
    assert any(event["type"] == "confirmation_required" for event in events)
    assert not any(event["type"] == "tool_started" for event in events)
    terminal = [
        event for event in events if event["type"] == "confirmation_resolved"
    ][-1]
    assert terminal["status"] == terminal_status
    assert terminal["tool_invoked"] is False
    assert terminal["reason_code"]
    assert terminal["audit_ref"].startswith("audit-")


def test_chat_pending_and_decision_require_current_principal_and_session() -> None:
    store = CapabilityStore("sqlite:///:memory:", Path("."))
    confirmations = ConfirmationService(store, PreToolCallGate(store))
    pending = Confirmation(
        id="confirmation-chat-bound",
        principal="alice",
        session_id="session-a",
        turn_id="turn-a",
        call_id="call-a",
        request_hash="a" * 64,
        server_id="builtin",
        tool_name="write_job",
        schema_hash="b" * 64,
        arguments_summary={"target": "local"},
        risk="write",
        destination="local",
        expires_at=confirmations._now(),
    ).model_copy(
        update={
            "expires_at": confirmations._now().replace(year=2099),
        }
    )
    store.create_confirmation(pending)
    other_session = pending.model_copy(
        update={
            "id": "confirmation-chat-bound-other-session",
            "session_id": "session-b",
            "turn_id": "turn-b",
            "call_id": "call-b",
            "request_hash": "c" * 64,
        }
    )
    store.create_confirmation(other_session)
    management = pending.model_copy(
        update={
            "id": "confirmation-management-only",
            "session_id": "management",
            "turn_id": "operation-management",
            "call_id": "operation-management",
            "request_hash": "d" * 64,
            "server_id": "management",
            "tool_name": "server.connect",
        }
    )
    store.create_confirmation(management)
    chat_using_reserved_session = pending.model_copy(
        update={
            "id": "confirmation-chat-reserved-session",
            "session_id": "management",
            "turn_id": "turn-reserved",
            "call_id": "call-reserved",
            "request_hash": "e" * 64,
        }
    )
    store.create_confirmation(chat_using_reserved_session)
    services = CapabilityApiServices(
        manager=SimpleNamespace(),
        registry=SimpleNamespace(),
        skill_registry=None,
        confirmations=confirmations,
        store=store,
        application=None,
    )
    app = FastAPI()
    app.include_router(create_capabilities_router())
    app.dependency_overrides[get_capability_services] = lambda: services
    app.dependency_overrides[get_management_principal] = lambda: ManagementPrincipal(
        subject="alice", role="admin"
    )

    with TestClient(app) as client:
        missing_session = client.get(
            "/v1/capabilities/confirmations/pending",
        )
        visible = client.get(
            "/v1/capabilities/confirmations/pending",
            params={"session_id": "session-a"},
        )
        management_visible = client.get(
            "/v1/capabilities/confirmations/pending",
            params={"session_id": "management"},
        )
        wrong_session = client.post(
            f"/v1/capabilities/confirmations/{pending.id}/decisions",
            json={
                "expected_revision": pending.revision,
                "decision": "once",
                "idempotency_key": "stable-wrong-session-key",
                "session_id": "session-b",
                },
            )
        reserved_session = client.post(
            (
                "/v1/capabilities/confirmations/"
                f"{chat_using_reserved_session.id}/decisions"
            ),
            json={
                "expected_revision": chat_using_reserved_session.revision,
                "decision": "cancel",
                "idempotency_key": "stable-reserved-session-key",
                "session_id": "management",
            },
        )

    assert missing_session.status_code == 422
    assert [item["id"] for item in visible.json()["confirmations"]] == [pending.id]
    assert [
        item["id"] for item in management_visible.json()["confirmations"]
    ] == [management.id]
    assert wrong_session.status_code == 403
    assert wrong_session.json()["detail"]["code"] == "confirmation_session_mismatch"
    assert reserved_session.status_code == 403
    assert reserved_session.json()["detail"]["code"] == "confirmation_namespace_mismatch"
    assert confirmations.get(pending.id).status == "pending"
    assert confirmations.get(chat_using_reserved_session.id).status == "pending"

    app.dependency_overrides[get_management_principal] = lambda: ManagementPrincipal(
        subject="mallory", role="admin"
    )
    with TestClient(app) as client:
        hidden = client.get(
            "/v1/capabilities/confirmations/pending",
            params={"session_id": "session-a"},
        )
        denied = client.post(
            f"/v1/capabilities/confirmations/{pending.id}/decisions",
            json={
                "expected_revision": pending.revision,
                "decision": "cancel",
                "idempotency_key": "stable-other-principal-key",
                "session_id": "session-a",
            },
        )

    assert hidden.json() == {"confirmations": []}
    assert denied.status_code == 403
    assert confirmations.get(pending.id).status == "pending"
