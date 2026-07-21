from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from mcp import types

from starter_agent.capabilities.store import CapabilityStore
from starter_agent.mcp.client import ClientMetadata
from starter_agent.mcp.config import McpConfiguration, McpServerConfig
from starter_agent.mcp.discovery import DiscoveryError
from starter_agent.mcp.manager import McpManager


class _SnapshotSession:
    def __init__(self) -> None:
        self.invalid_schema = False

    async def list_tools(self, *, params=None):
        schema = (
            {"type": "not-a-real-json-schema-type"}
            if self.invalid_schema
            else {"type": "object", "properties": {}}
        )
        return types.ListToolsResult(
            tools=[types.Tool(name="find_jobs", inputSchema=schema)]
        )

    async def list_resources(self, *, params=None):
        return types.ListResourcesResult(resources=[])

    async def list_resource_templates(self, *, params=None):
        return types.ListResourceTemplatesResult(
            resourceTemplates=[
                types.ResourceTemplate(
                    name="job-detail",
                    uriTemplate="jobs://detail/{job_id}",
                    description="A persisted job detail template",
                    mimeType="application/json",
                )
            ]
        )

    async def list_prompts(self, *, params=None):
        return types.ListPromptsResult(prompts=[])

    async def send_ping(self):
        return types.EmptyResult()


class _SnapshotClient:
    def __init__(self) -> None:
        self._session = _SnapshotSession()
        self.session = None
        self.stderr_summary = ""

    async def connect(self) -> ClientMetadata:
        self.session = self._session
        return ClientMetadata(
            protocol_version="2025-06-18",
            runtime_name="snapshot-fixture",
            runtime_version="1.0.0",
            node_version="v22.1.0",
            npx_version="10.8.0",
            started_at=datetime.now(UTC),
        )

    async def run_session_command(self, command):
        return await command(self.session)

    async def close(self) -> None:
        self.session = None


@pytest.mark.asyncio
async def test_snapshot_activation_is_atomic_and_survives_restart(
    tmp_path: Path,
) -> None:
    database_url = "sqlite:///capabilities.db"
    client = _SnapshotClient()
    store = CapabilityStore(database_url, tmp_path)
    manager = McpManager(
        McpConfiguration(
            source_path=tmp_path / "mcp.json",
            servers={"alpha": McpServerConfig(command="npx")},
            config_hash="a" * 64,
        ),
        store=store,
        client_factory=lambda _server_id, _config: client,
    )
    await manager.start()

    first = await manager.discover("alpha")
    first_summary = manager.get_snapshot_summary("alpha")
    assert first.active is True
    assert first_summary is not None
    assert first_summary.version == 1
    assert first_summary.tool_count == 1
    assert first_summary.resource_count == 1
    assert store.list_tools(first.id)[0].enabled is False
    assert store.list_tools(first.id)[0].review_state == "unreviewed"
    template = store.list_resources(first.id)[0]
    assert template.uri is None
    assert template.uri_template == "jobs://detail/{job_id}"
    assert template.metadata["capability_kind"] == "resource_template"

    client.session.invalid_schema = True
    with pytest.raises(DiscoveryError) as raised:
        await manager.discover("alpha")
    assert raised.value.code == "invalid_tool_schema"
    assert manager.get_snapshot_summary("alpha") == first_summary

    client.session.invalid_schema = False
    with pytest.raises(DiscoveryError) as raised:
        await manager.discover(
            "alpha",
            reserved_model_names={"mcp__alpha__find_jobs"},
        )
    assert raised.value.code == "model_alias_collision"
    assert manager.get_snapshot_summary("alpha") == first_summary

    store.close()
    reopened = CapabilityStore(database_url, tmp_path)
    persisted = reopened.get_active_snapshot("alpha")
    assert persisted == first
    assert reopened.get_snapshot_summary("alpha") == first_summary
    persisted_template = reopened.list_resources(first.id)[0]
    assert persisted_template.uri_template == "jobs://detail/{job_id}"
