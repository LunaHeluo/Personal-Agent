from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from starter_agent.capabilities.confirmations import ConfirmationService
from starter_agent.capabilities.gate import PreToolCallGate
from starter_agent.capabilities.models import Server
from starter_agent.capabilities.registry import ExecutionCapability
from starter_agent.capabilities.store import CapabilityStore
from starter_agent.interfaces.capabilities_api import (
    CapabilityApiServices,
    ManagementPrincipal,
    create_capabilities_router,
    get_capability_services,
    get_management_principal,
)
from starter_agent.skills.models import SkillDefinition, SkillSnapshot


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
        context = client.get(f"/v1/capabilities/context-snapshots/{uuid4()}")
        assert context.status_code == 200
        assert context.json()["tool_context_revision"] == 7

        tool_proposal = client.post(
            "/v1/capabilities/tools/write_job/enable",
            json={"expected_revision": 7},
        )
        skill_proposal = client.post(
            "/v1/capabilities/skills/research/reload",
            json={"expected_revision": 4},
        )

    assert tool_proposal.status_code == 202
    assert tool_proposal.json()["confirmation"]["arguments_summary"]["diff"]
    assert registry.enable_calls == 0
    assert skill_proposal.status_code == 202
    assert skill_proposal.json()["confirmation"]["arguments_summary"]["risk"]
    assert skills.reload_calls == 0
