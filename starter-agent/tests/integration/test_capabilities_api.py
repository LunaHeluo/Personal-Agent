from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from starter_agent.capabilities.confirmations import ConfirmationService
from starter_agent.capabilities.gate import PreToolCallGate
from starter_agent.capabilities.models import (
    AuditEvent,
    PolicyRule,
    Server,
    canonical_json_sha256,
)
from starter_agent.capabilities.registry import ExecutionCapability
from starter_agent.capabilities.registry import UnifiedToolRegistry
from starter_agent.capabilities.store import CapabilityStore
from starter_agent.interfaces.capabilities_api import (
    CapabilityApiServices,
    ManagementPrincipal,
    create_capabilities_router,
    get_capability_services,
    get_management_principal,
)
from starter_agent.skills.models import SkillDefinition, SkillSnapshot
from starter_agent.tools.registry import ToolRegistry


class _Manager:
    def __init__(self) -> None:
        self.server = Server(
            id="alpha",
            name="alpha",
            config_source="mcp.json",
            config_hash="a" * 64,
        )

    def statuses(self):
        return {"alpha": self.server}

    def get_status(self, server_id):
        if server_id != "alpha":
            raise KeyError(server_id)
        return self.server

    def get_snapshot_summary(self, _server_id):
        return None


def test_server_list_is_read_from_manager() -> None:
    manager = _Manager()
    registry = SimpleNamespace(context_revision=0, lightweight_catalog=lambda: None)
    services = CapabilityApiServices(
        manager=manager,
        registry=registry,
        skill_registry=None,
        confirmations=None,
        store=None,
        application=None,
    )
    app = FastAPI()
    app.include_router(create_capabilities_router())
    app.dependency_overrides[get_capability_services] = lambda: services
    app.dependency_overrides[get_management_principal] = lambda: ManagementPrincipal(
        subject="test-admin", role="admin"
    )

    with TestClient(app) as client:
        response = client.get("/v1/capabilities/servers")

    assert response.status_code == 200
    assert response.json()["servers"][0]["id"] == "alpha"
    assert response.json()["servers"][0]["revision"] == 0


def test_authoritative_tool_skill_trace_context_queries_and_dangerous_barriers() -> None:
    manager = _Manager()
    store = CapabilityStore("sqlite:///:memory:", Path("."))
    confirmations = ConfirmationService(store, PreToolCallGate(store))
    skill = SkillDefinition(
        name="research",
        description="Research jobs",
        version="1",
        source="project",
        source_path="skills/research/SKILL.md",
        enabled=True,
        trigger_examples=("research jobs",),
        negative_examples=("send email",),
        validation=("return sources",),
        failure_policy=("fail closed",),
        definition="# Research",
        snapshot_hash="b" * 64,
    )

    class Registry:
        context_revision = 7

        def __init__(self):
            self.enable_calls = 0

        def resolve_execution(self, name):
            if name != "write_job":
                return None
            return ExecutionCapability(
                server_id="alpha",
                canonical_name="write_job",
                model_alias="write_job",
                snapshot_id="snapshot-alpha",
                schema_hash="c" * 64,
                input_schema={"type": "object"},
                metadata={},
                risk_level="write",
                enabled=False,
                connected=True,
                review_state="approved",
                browser=False,
            )

        def set_tool_enabled(self, _name, _enabled):
            self.enable_calls += 1

        def model_snapshot(self):
            return SimpleNamespace(
                context_revision=7,
                provider_tools=lambda: [
                    {
                        "type": "function",
                        "function": {
                            "name": "write_job",
                            "parameters": {"type": "object"},
                        },
                    }
                ],
            )

    class Skills:
        def __init__(self):
            self.reload_calls = 0
            self.value = SkillSnapshot(
                revision=4,
                skills=(skill,),
                loaded_at=datetime.now(UTC),
            )

        def snapshot(self):
            return self.value

        def get(self, name):
            return skill if name == skill.name else None

        def prepare_reload(self, _name):
            return skill

        def reload(self):
            self.reload_calls += 1
            return self.value

    registry, skills = Registry(), Skills()
    application = SimpleNamespace(latest_summary_trace=lambda _session_id: None)
    services = CapabilityApiServices(
        manager=manager,
        registry=registry,
        skill_registry=skills,
        confirmations=confirmations,
        store=store,
        application=application,
    )
    app = FastAPI()
    app.include_router(create_capabilities_router())
    app.dependency_overrides[get_capability_services] = lambda: services
    app.dependency_overrides[get_management_principal] = lambda: ManagementPrincipal(
        subject="test-admin", role="admin"
    )

    with TestClient(app) as client:
        assert client.get("/v1/capabilities/servers/alpha").status_code == 200
        tool = client.get("/v1/capabilities/tools/write_job")
        assert tool.status_code == 200
        assert tool.json()["tool"]["schema"] == {"type": "object"}
        assert client.get("/v1/capabilities/skills").json()["revision"] == 4
        assert client.get("/v1/capabilities/skills/research/raw").json()[
            "definition"
        ] == "# Research"
        assert client.get("/v1/capabilities/traces").json() == {"traces": []}
        context = client.get(
            f"/v1/capabilities/context-snapshots/{uuid4()}",
            params={"turn_id": "turn-1", "revision": 7},
        )
        assert context.status_code == 404

        tool_proposal = client.post(
            "/v1/capabilities/tools/write_job/enable",
            json={"expected_revision": 7},
        )
        skill_proposal = client.post(
            "/v1/capabilities/skills/research/reload",
            json={"expected_revision": 4},
        )
        raw_as_admin = client.get("/v1/capabilities/skills/research/raw")

    assert tool_proposal.status_code == 202
    assert tool_proposal.json()["confirmation"]["arguments_summary"]["diff"]
    assert registry.enable_calls == 0
    assert skill_proposal.status_code == 202
    assert skill_proposal.json()["confirmation"]["arguments_summary"]["risk"]
    assert skills.reload_calls == 0
    assert raw_as_admin.status_code == 200


def test_skill_detail_is_lightweight_and_raw_requires_admin() -> None:
    skill = SkillDefinition(
        name="research",
        description="Research jobs",
        version="1",
        source="project",
        source_path="skills/research/SKILL.md",
        enabled=True,
        trigger_examples=("research jobs",),
        negative_examples=("send email",),
        validation=("return sources",),
        failure_policy=("fail closed",),
        definition="# Research\nsecret=should-not-leak",
        snapshot_hash="b" * 64,
    )
    skills = SimpleNamespace(
        get=lambda _name: skill,
        snapshot=lambda: SkillSnapshot(
            revision=1, skills=(skill,), loaded_at=datetime.now(UTC)
        ),
    )
    services = CapabilityApiServices(
        manager=SimpleNamespace(),
        registry=SimpleNamespace(context_revision=0),
        skill_registry=skills,
        confirmations=None,
        store=None,
        application=None,
    )
    app = FastAPI()
    app.include_router(create_capabilities_router())
    app.dependency_overrides[get_capability_services] = lambda: services
    app.dependency_overrides[get_management_principal] = lambda: ManagementPrincipal(
        subject="viewer", role="viewer"
    )
    with TestClient(app) as client:
        detail = client.get("/v1/capabilities/skills/research")
        raw = client.get("/v1/capabilities/skills/research/raw")

    assert detail.status_code == 200
    assert "definition" not in detail.text
    assert raw.status_code == 403


def test_health_policy_trace_and_context_endpoints_use_authoritative_state() -> None:
    store = CapabilityStore("sqlite:///:memory:", Path("."))

    class Manager(_Manager):
        def __init__(self):
            super().__init__()
            self.ping_calls = 0

        async def ping(self, _server_id):
            self.ping_calls += 1
            self.server = self.server.model_copy(
                update={
                    "health_state": "healthy",
                    "revision": self.server.revision + 1,
                }
            )
            return self.server

    class Registry:
        context_revision = 7

        def resolve_execution(self, _name):
            return ExecutionCapability(
                server_id="alpha",
                canonical_name="write_job",
                model_alias="write_job",
                snapshot_id="snapshot-alpha",
                schema_hash="c" * 64,
                input_schema={"type": "object"},
                metadata={},
                risk_level="write",
                enabled=True,
                connected=True,
                review_state="approved",
                browser=False,
            )

        def model_snapshot(self):
            return SimpleNamespace(
                context_revision=7,
                provider_tools=lambda: [
                    {
                        "type": "function",
                        "function": {
                            "name": "write_job",
                            "parameters": {"type": "object"},
                        },
                    }
                ],
            )

        def notify_policy_changed(self):
            return self.model_snapshot()

    manager, registry = Manager(), Registry()
    rule = PolicyRule(
        id="policy-one",
        server_id="alpha",
        tool_name="write_job",
        effect="deny",
        created_by="test-admin",
    )
    store.create_policy_rule(rule)
    session_id = uuid4()
    store.append_audit_event(
        AuditEvent(
            event_id="audit-context-snapshot",
            actor="runtime",
            action="model.context.snapshot",
            target="provider:test:model",
            decision="allow",
            reason_code="provider_request_prepared",
            session_id=str(session_id),
            turn_id="turn-1",
            call_id="model-call-1",
            payload={
                "model_call": 1,
                "context_revision": 6,
                "provider_tools_hash": canonical_json_sha256(
                    [
                        {
                            "type": "function",
                            "function": {
                                "name": "write_job",
                                "parameters": {"type": "object"},
                            },
                        }
                    ]
                ),
                "callable_tools": [
                    {
                        "name": "write_job",
                        "schema_hash": canonical_json_sha256({"type": "object"}),
                    }
                ],
            },
            created_at=datetime.now(UTC),
        )
    )
    services = CapabilityApiServices(
        manager=manager,
        registry=registry,
        skill_registry=None,
        confirmations=ConfirmationService(store, PreToolCallGate(store)),
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
        health = client.post(
            "/v1/capabilities/servers/alpha/health-check",
            json={"expected_revision": 0},
        )
        policies = client.get("/v1/capabilities/tools/write_job/policies")
        deleted = client.request(
            "DELETE",
            "/v1/capabilities/tools/write_job/policies/policy-one",
            json={"expected_revision": 0},
        )
        context = client.get(
            f"/v1/capabilities/context-snapshots/{session_id}",
            params={"turn_id": "turn-1", "revision": 6},
        )
        traces = client.get(
            "/v1/capabilities/traces", params={"turn_id": "turn-1"}
        )

    assert health.status_code == 200
    assert manager.ping_calls == 1
    assert policies.json()["policies"][0]["id"] == rule.id
    assert deleted.status_code == 200
    assert store.get_policy_rule(rule.id) is None
    assert context.json()["callable_tools"] == [
        {
            "name": "write_job",
            "schema_hash": canonical_json_sha256({"type": "object"}),
        }
    ]
    assert traces.status_code == 200


def test_builtin_enable_override_is_cas_persistent_and_review_is_stable_4xx() -> None:
    store = CapabilityStore("sqlite:///:memory:", Path("."))
    registry = UnifiedToolRegistry(ToolRegistry(["get_current_time"]))
    services = CapabilityApiServices(
        manager=_Manager(),
        registry=registry,
        skill_registry=None,
        confirmations=ConfirmationService(
            store, PreToolCallGate(store, registry=registry)
        ),
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
        disabled = client.post(
            "/v1/capabilities/tools/get_current_time/disable",
            json={"expected_revision": 0},
        )
        stale = client.post(
            "/v1/capabilities/tools/get_current_time/disable",
            json={"expected_revision": 0},
        )
        review = client.post(
            "/v1/capabilities/tools/get_current_time/review",
            json={"expected_revision": 1, "review_state": "approved"},
        )
        invalid = client.post(
            "/v1/capabilities/tools/get_current_time/disable",
            json={"expected_revision": -1},
        )

    assert disabled.status_code == 200
    assert disabled.json()["revision"] == 1
    assert registry.model_snapshot().provider_tools() == []
    assert stale.status_code == 409
    assert review.status_code == 409
    assert review.json()["detail"]["code"] == "builtin_review_unsupported"
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["authoritative_state"]["operation_id"]
    assert any(
        event.reason_code == "validation_error"
        for event in store.list_audit_events()
    )
    restarted = UnifiedToolRegistry(ToolRegistry(["get_current_time"]))
    for override in store.list_builtin_tool_overrides():
        restarted.set_tool_enabled(override.tool_name, override.enabled)
    assert restarted.resolve_execution("get_current_time").enabled is False
