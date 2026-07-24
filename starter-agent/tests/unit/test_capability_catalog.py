from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from starter_agent.capabilities.models import (
    Server,
    Snapshot,
    Tool,
    canonical_json_sha256,
)
from starter_agent.capabilities.registry import UnifiedToolRegistry
from starter_agent.capabilities.store import CapabilityStore
from starter_agent.interfaces.capabilities_api import (
    CapabilityApiServices,
    ManagementPrincipal,
    create_capabilities_router,
    get_capability_services,
    get_management_principal,
)
from starter_agent.tools.registry import ToolRegistry


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CATALOG = PROJECT_ROOT / "docs" / "capability_catalog.md"
OPERATIONS = PROJECT_ROOT / "docs" / "job-research-operations.md"
ACCEPTANCE = PROJECT_ROOT / "docs" / "job-research-acceptance.md"


def _server(
    *,
    runtime_name: str = "@playwright/mcp",
    runtime_version: str = "test-runtime-version",
) -> Server:
    return Server(
        id="playwright",
        name="playwright",
        config_source="config/mcp.json",
        config_hash="a" * 64,
        connection_state="ready",
        runtime_name=runtime_name,
        runtime_version=runtime_version,
        stderr_summary="authorization=Bearer must-not-leak",
    )


def _discovered_tool() -> Tool:
    schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "token": {"type": "string", "default": "must-not-leak"},
        },
    }
    return Tool(
        snapshot_id="playwright-snapshot-1",
        server_id="playwright",
        upstream_name="observed_public_page_reader",
        model_alias="mcp__playwright__observed_public_page_reader",
        description="login=jane@example.com resume=private resume body",
        input_schema=schema,
        schema_hash=canonical_json_sha256(schema),
        metadata={
            "cookie": "session=must-not-leak",
            "authorization": "Bearer must-not-leak",
        },
        outbound_scope=("public_url",),
        enabled=True,
    )


def _manager(store: CapabilityStore, server: Server) -> SimpleNamespace:
    return SimpleNamespace(
        store=store,
        statuses=lambda: {server.id: store.get_server(server.id)},
    )


def _registry_from_store(
    store: CapabilityStore,
    server: Server,
    *,
    builtins: list[str] | None = None,
) -> UnifiedToolRegistry:
    registry = UnifiedToolRegistry(ToolRegistry(builtins or []))
    registry.refresh_from_manager(_manager(store, server))
    return registry


def _create_snapshot(
    store: CapabilityStore,
    *,
    activate: bool,
    stale: bool = False,
) -> tuple[Snapshot, Tool]:
    tool = _discovered_tool()
    snapshot = Snapshot(
        id=tool.snapshot_id,
        server_id=tool.server_id,
        version=1,
        schema_hash="b" * 64,
        discovered_at=datetime(2026, 7, 24, tzinfo=UTC),
        tool_count=1,
    )
    store.create_snapshot(snapshot, tools=(tool,))
    if activate:
        store.activate_snapshot(snapshot.server_id, snapshot.id)
    if stale:
        store.mark_active_snapshot_stale(snapshot.server_id, error="refresh_failed")
    return snapshot, tool


def test_catalog_export_matches_store_reviewed_tool_identity_and_schema(
    tmp_path: Path,
) -> None:
    store = CapabilityStore("sqlite:///:memory:", tmp_path)
    server = _server()
    store.create_server(server)
    snapshot, tool = _create_snapshot(store, activate=True)
    reviewed = store.update_tool(
        snapshot.id,
        tool.upstream_name,
        expected_revision=0,
        review_state="approved",
    )
    registry = _registry_from_store(
        store,
        server,
        builtins=["get_current_time"],
    )

    exported = registry.catalog_export()

    assert exported["authority"] == "runtime_registry"
    assert exported["context_revision"] == registry.context_revision
    exported_tool = exported["mcp_servers"][0]["tools"][0]
    assert exported_tool["upstream_name"] == reviewed.upstream_name
    assert exported_tool["model_alias"] == reviewed.model_alias
    assert exported_tool["schema_hash"] == reviewed.schema_hash
    assert exported_tool["reviewed_at"] == reviewed.reviewed_at.isoformat().replace(
        "+00:00", "Z"
    )
    assert exported_tool["review_conclusion"] == "approved"
    assert exported_tool["reviewed"] is True
    assert exported["mcp_servers"][0]["discovery_state"] == "discovered"
    assert exported["mcp_servers"][0]["review_state"] == "approved"
    serialized = json.dumps(exported, ensure_ascii=False).casefold()
    for forbidden in (
        "must-not-leak",
        "bearer",
        "cookie",
        "private resume body",
        "jane@example.com",
        "input_schema",
        "description",
        "metadata",
        "stderr",
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize("snapshot_state", ["inactive", "stale"])
def test_catalog_export_hides_inactive_or_stale_snapshot_capabilities(
    tmp_path: Path,
    snapshot_state: str,
) -> None:
    store = CapabilityStore("sqlite:///:memory:", tmp_path)
    server = _server()
    store.create_server(server)
    snapshot, tool = _create_snapshot(
        store,
        activate=snapshot_state == "stale",
        stale=snapshot_state == "stale",
    )
    registry = _registry_from_store(store, server)

    exported = registry.catalog_export()

    server_export = exported["mcp_servers"][0]
    assert server_export["discovery_state"] == "not_discovered"
    assert server_export["review_state"] == "not_reviewed"
    assert server_export["snapshot_id"] is None
    assert server_export["snapshot_version"] is None
    assert server_export["discovered_at"] is None
    assert server_export["tools"] == []
    serialized = json.dumps(exported, ensure_ascii=False)
    assert tool.upstream_name not in serialized
    assert tool.model_alias not in serialized
    assert tool.schema_hash not in serialized
    if snapshot_state == "stale":
        assert store.get_active_snapshot(snapshot.server_id).stale is True


def test_catalog_export_reports_store_without_snapshot_as_not_discovered(
    tmp_path: Path,
) -> None:
    store = CapabilityStore("sqlite:///:memory:", tmp_path)
    server = _server()
    store.create_server(server)
    registry = _registry_from_store(store, server)

    exported = registry.catalog_export()

    assert exported["mcp_servers"][0]["discovery_state"] == "not_discovered"
    assert exported["mcp_servers"][0]["review_state"] == "not_reviewed"
    assert exported["mcp_servers"][0]["tools"] == []
    assert "schema_hash" not in json.dumps(exported)


@pytest.mark.parametrize(
    "sensitive",
    [
        "auth=top-secret",
        "authorization: Basic dXNlcjpwYXNz",
        "userinfo=user:pass",
        "https://user:pass@example.test/private",
        "user:pass",
        "token=top-secret",
        "cookie=session-value",
        "jane@example.com",
        "email=private-user",
        "login=user:pass",
        "resume=private resume body",
    ],
)
def test_catalog_export_and_viewer_api_redact_sensitive_allowlisted_text(
    tmp_path: Path,
    sensitive: str,
) -> None:
    store = CapabilityStore("sqlite:///:memory:", tmp_path)
    server = _server(runtime_name=sensitive, runtime_version=sensitive)
    store.create_server(server)
    registry = _registry_from_store(store, server)
    services = CapabilityApiServices(
        manager=_manager(store, server),
        registry=registry,
        skill_registry=None,
        confirmations=None,
        store=store,
        application=None,
    )
    app = FastAPI()
    app.include_router(create_capabilities_router())
    app.dependency_overrides[get_capability_services] = lambda: services
    app.dependency_overrides[get_management_principal] = lambda: ManagementPrincipal(
        subject="catalog-viewer", role="viewer"
    )

    exported = registry.catalog_export()
    with TestClient(app) as client:
        response = client.get("/v1/capabilities/catalog/export")

    assert exported["mcp_servers"][0]["runtime_name"] == "<redacted>"
    assert exported["mcp_servers"][0]["runtime_version"] == "<redacted>"
    assert response.status_code == 200
    assert response.json() == exported
    assert sensitive.casefold() not in response.text.casefold()


def test_capability_catalog_documents_required_fields_without_fake_snapshot() -> None:
    catalog = CATALOG.read_text(encoding="utf-8")
    required_fields = (
        "Source",
        "Config version",
        "Runtime version",
        "Transport",
        "Capability",
        "Enabled state",
        "Context exposure",
        "Risk",
        "Allowlist",
        "Always confirm",
        "Outbound data",
        "Owner",
        "Health check",
        "Disable",
    )
    for field in required_fields:
        assert field in catalog
    assert "runtime_registry" in catalog
    assert "not_discovered" in catalog
    assert "not_reviewed" in catalog
    playwright_section = catalog.split("## Playwright MCP", 1)[1].split("\n## ", 1)[0]
    assert not re.search(r"\b[0-9a-f]{64}\b", playwright_section)
    assert "mcp__playwright__browser_" not in playwright_section


def test_operations_and_acceptance_docs_define_observable_evidence() -> None:
    operations = OPERATIONS.read_text(encoding="utf-8")
    for stage in (
        "Node",
        "npx",
        "cache",
        "process",
        "initialize",
        "discovery",
        "browser dependencies",
        "Gate",
        "confirmation",
        "Tool Result",
        "refresh",
    ):
        assert stage in operations
    assert "Observable state" in operations
    assert "Stable error code" in operations
    assert "Minimal retry" in operations

    acceptance = ACCEPTANCE.read_text(encoding="utf-8")
    for phrase in (
        "Real success record",
        "MCP unavailable degradation record",
        "Evidence location",
        "Mock",
        "configuration exists",
        "model narration",
        "NOT PASS",
        "not_recorded",
    ):
        assert phrase in acceptance
