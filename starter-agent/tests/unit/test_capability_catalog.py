from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from starter_agent.capabilities.models import (
    Server,
    Snapshot,
    Tool,
    canonical_json_sha256,
)
from starter_agent.capabilities.registry import UnifiedToolRegistry
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


def _server() -> Server:
    return Server(
        id="playwright",
        name="playwright",
        config_source="config/mcp.json",
        config_hash="a" * 64,
        connection_state="ready",
        runtime_name="@playwright/mcp",
        runtime_version="test-runtime-version",
        stderr_summary="authorization=Bearer must-not-leak",
    )


def _reviewed_tool() -> Tool:
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
        review_state="approved",
    )


def test_catalog_export_contains_only_allowlisted_reviewed_runtime_fields() -> None:
    registry = UnifiedToolRegistry(ToolRegistry(["get_current_time"]))
    tool = _reviewed_tool()
    snapshot = Snapshot(
        id=tool.snapshot_id,
        server_id=tool.server_id,
        version=1,
        schema_hash="b" * 64,
        discovered_at=datetime(2026, 7, 24, tzinfo=UTC),
        active=True,
        tool_count=1,
    )
    registry.refresh_server(_server(), [tool], snapshot=snapshot)

    exported = registry.catalog_export()

    assert exported["authority"] == "runtime_registry"
    assert exported["context_revision"] == registry.context_revision
    assert exported["builtins"] == [
        {
            "name": "get_current_time",
            "server_id": "builtin",
            "capability_type": "builtin",
            "enabled": True,
            "review_state": "approved",
            "schema_hash": canonical_json_sha256(
                ToolRegistry(["get_current_time"]).get(
                    "get_current_time"
                ).input_schema
            ),
        }
    ]
    assert exported["mcp_servers"][0]["discovery_state"] == "discovered"
    assert exported["mcp_servers"][0]["tools"] == [
        {
            "upstream_name": tool.upstream_name,
            "model_alias": tool.model_alias,
            "schema_hash": tool.schema_hash,
            "review_state": "approved",
            "enabled": True,
        }
    ]
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


def test_catalog_export_does_not_invent_schema_for_undiscovered_server() -> None:
    registry = UnifiedToolRegistry(ToolRegistry([]))
    registry.refresh_server(_server(), [])

    exported = registry.catalog_export()

    assert exported["mcp_servers"] == [
        {
            "server_id": "playwright",
            "capability_type": "mcp",
            "enabled": True,
            "connection_state": "ready",
            "health_state": "unknown",
            "transport": "stdio",
            "runtime_name": "@playwright/mcp",
            "runtime_version": "test-runtime-version",
            "discovery_state": "not_discovered",
            "review_state": "not_reviewed",
            "snapshot_id": None,
            "snapshot_version": None,
            "discovered_at": None,
            "tools": [],
        }
    ]
    assert "schema_hash" not in json.dumps(exported)


def test_catalog_export_redacts_sensitive_values_in_allowlisted_text_fields() -> None:
    registry = UnifiedToolRegistry(ToolRegistry([]))
    server = _server().model_copy(
        update={
            "runtime_name": "login=jane@example.com",
            "runtime_version": "authorization: Bearer top-secret",
        }
    )
    registry.refresh_server(server, [])

    exported = registry.catalog_export()

    assert exported["mcp_servers"][0]["runtime_name"] == "<redacted>"
    assert exported["mcp_servers"][0]["runtime_version"] == "<redacted>"
    serialized = json.dumps(exported, ensure_ascii=False).casefold()
    assert "jane@example.com" not in serialized
    assert "top-secret" not in serialized
    assert "bearer" not in serialized


def test_catalog_export_api_is_read_only_and_uses_registry_authority() -> None:
    registry = UnifiedToolRegistry(ToolRegistry(["get_current_time"]))
    services = CapabilityApiServices(
        manager=object(),
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
        subject="catalog-viewer", role="viewer"
    )

    with TestClient(app) as client:
        response = client.get("/v1/capabilities/catalog/export")

    assert response.status_code == 200
    assert response.json() == registry.catalog_export()


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
