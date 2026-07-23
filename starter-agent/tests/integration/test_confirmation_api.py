from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from starter_agent.capabilities.confirmations import ConfirmationService
from starter_agent.capabilities.gate import PreToolCallGate
from starter_agent.capabilities.models import Confirmation
from starter_agent.capabilities.models import Server
from starter_agent.capabilities.store import CapabilityStore
from starter_agent.interfaces.capabilities_api import (
    CapabilityApiServices,
    ManagementPrincipal,
    create_capabilities_router,
    get_capability_services,
    get_management_principal,
)


def test_pending_confirmations_are_read_from_persistent_store() -> None:
    store = CapabilityStore("sqlite:///:memory:", Path("."))
    gate = PreToolCallGate(store)
    confirmations = ConfirmationService(store, gate)
    pending = Confirmation(
        id="confirmation-api",
        principal="local-user",
        session_id="management",
        turn_id="operation-1",
        call_id="operation-1",
        request_hash="a" * 64,
        server_id="management",
        tool_name="server.connect",
        schema_hash="b" * 64,
        arguments_summary={
            "operation": "server.connect",
            "target": "alpha",
            "diff": {"connection_state": ["closed", "ready"]},
            "risk": "external_process",
            "impact": ["server:alpha"],
            "expected_revision": 0,
        },
        risk="dangerous",
        destination="server:alpha",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    store.create_confirmation(pending)
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
        subject="test-admin", role="admin"
    )

    with TestClient(app) as client:
        response = client.get("/v1/capabilities/confirmations/pending")

    assert response.status_code == 200
    assert response.json()["confirmations"][0]["id"] == pending.id
    assert response.json()["confirmations"][0]["arguments_summary"]["diff"]


def test_connect_confirmation_is_zero_mutation_then_exactly_once() -> None:
    store = CapabilityStore("sqlite:///:memory:", Path("."))
    gate = PreToolCallGate(store)
    confirmations = ConfirmationService(store, gate)

    class Manager:
        def __init__(self):
            self.state = Server(
                id="alpha",
                name="alpha",
                config_source="mcp.json",
                config_hash="a" * 64,
            )
            self.connect_calls = 0

        def get_status(self, _server_id):
            return self.state

        async def connect(self, _server_id):
            self.connect_calls += 1
            self.state = self.state.model_copy(
                update={"connection_state": "ready", "revision": 1}
            )
            return self.state

    manager = Manager()
    registry = SimpleNamespace(
        context_revision=0,
        refresh_from_manager=lambda _manager: None,
    )
    services = CapabilityApiServices(
        manager=manager,
        registry=registry,
        skill_registry=None,
        confirmations=confirmations,
        store=store,
        application=None,
    )
    app = FastAPI()
    app.include_router(create_capabilities_router())
    app.dependency_overrides[get_capability_services] = lambda: services
    app.dependency_overrides[get_management_principal] = lambda: ManagementPrincipal(
        subject="test-admin", role="admin"
    )

    with TestClient(app) as client:
        proposed = client.post(
            "/v1/capabilities/servers/alpha/connect",
            json={"expected_revision": 0},
        )
        assert proposed.status_code == 202
        assert manager.connect_calls == 0
        confirmation = proposed.json()["confirmation"]
        approved = client.post(
            f"/v1/capabilities/confirmations/{confirmation['id']}/decisions",
            json={
                "expected_revision": confirmation["revision"],
                "decision": "once",
                "idempotency_key": "approve-alpha-once",
            },
        )
        assert approved.status_code == 200
        assert manager.connect_calls == 1
        replay = client.post(
            f"/v1/capabilities/confirmations/{confirmation['id']}/decisions",
            json={
                "expected_revision": confirmation["revision"],
                "decision": "once",
                "idempotency_key": "approve-alpha-once",
            },
        )

    assert replay.status_code == 409
    assert manager.connect_calls == 1
