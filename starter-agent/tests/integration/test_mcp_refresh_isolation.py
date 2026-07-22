from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from mcp import types

from starter_agent.capabilities.store import CapabilityStore
from starter_agent.mcp.client import ClientMetadata
from starter_agent.mcp.config import McpConfiguration, McpServerConfig
from starter_agent.mcp.manager import McpManager, McpManagerError


class _Session:
    def __init__(self, client: "_Client") -> None:
        self.client = client

    async def list_tools(self, *, params=None):
        self.client.discovery_started.set()
        await self.client.discovery_release.wait()
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name="find_jobs",
                    inputSchema={
                        "type": "object",
                        "properties": {"generation": {"const": self.client.generation}},
                    },
                )
            ]
        )

    async def list_resources(self, *, params=None):
        return types.ListResourcesResult(resources=[])

    async def list_resource_templates(self, *, params=None):
        return types.ListResourceTemplatesResult(resourceTemplates=[])

    async def list_prompts(self, *, params=None):
        return types.ListPromptsResult(prompts=[])


class _Client:
    def __init__(self, server_id: str, generation: int, *, blocked: bool) -> None:
        self.server_id = server_id
        self.generation = generation
        self._session = _Session(self)
        self.session = None
        self.stderr_summary = ""
        self.closed = False
        self.discovery_started = asyncio.Event()
        self.discovery_release = asyncio.Event()
        if not blocked:
            self.discovery_release.set()

    async def connect(self) -> ClientMetadata:
        self.session = self._session
        return ClientMetadata(
            protocol_version="2025-06-18",
            runtime_name=f"fixture-{self.server_id}",
            runtime_version=f"1.0.{self.generation}",
            node_version="v22.1.0",
            npx_version="10.8.0",
            started_at=datetime.now(UTC),
        )

    async def run_session_command(self, operation):
        return await operation(self.session)

    async def close(self) -> None:
        self.closed = True
        self.session = None


@pytest.mark.asyncio
async def test_refresh_is_per_server_and_leases_pin_client_generation(
    tmp_path: Path,
) -> None:
    created: dict[str, list[_Client]] = {"alpha": [], "beta": []}

    def factory(server_id: str, _config: McpServerConfig) -> _Client:
        generation = len(created[server_id]) + 1
        client = _Client(
            server_id,
            generation,
            blocked=server_id == "alpha" and generation in {2, 3},
        )
        created[server_id].append(client)
        return client

    configuration = McpConfiguration(
        source_path=tmp_path / "mcp.json",
        servers={
            "alpha": McpServerConfig(command="npx"),
            "beta": McpServerConfig(command="npx"),
        },
        config_hash="a" * 64,
    )
    manager = McpManager(
        configuration,
        store=CapabilityStore("sqlite:///:memory:", tmp_path),
        client_factory=factory,
        initialize_timeout_seconds=0.2,
        shutdown_timeout_seconds=0.05,
    )
    await manager.start()
    alpha_initial, beta_initial = await asyncio.gather(
        manager.discover("alpha"), manager.discover("beta")
    )
    alpha_revision = manager.get_status("alpha").revision
    beta_revision = manager.get_status("beta").revision
    alpha_old_lease = manager.lease("alpha")
    beta_old_lease = manager.lease("beta")
    alpha_old_binding = await alpha_old_lease.__aenter__()
    beta_old_binding = await beta_old_lease.__aenter__()
    assert alpha_old_binding.snapshot_id == alpha_initial.id
    assert beta_old_binding.snapshot_id == beta_initial.id

    alpha_refresh = asyncio.create_task(
        manager.refresh_server("alpha", alpha_revision)
    )
    while len(created["alpha"]) < 2:
        await asyncio.sleep(0)
    await created["alpha"][1].discovery_started.wait()
    with pytest.raises(McpManagerError) as duplicate:
        await manager.refresh_server("alpha", manager.get_status("alpha").revision)
    assert duplicate.value.code == "refresh_in_progress"

    beta_refresh = asyncio.create_task(
        manager.refresh_server("beta", beta_revision)
    )
    beta_snapshot = await beta_refresh
    assert beta_snapshot.server_id == "beta"
    assert created["beta"][0].closed is True
    assert manager.get_status("beta").error_code == "drain_timeout"
    assert alpha_refresh.done() is False

    created["alpha"][1].discovery_release.set()
    for _ in range(20):
        if manager._get_handle("alpha").client is created["alpha"][1]:
            break
        await asyncio.sleep(0)
    async with manager.lease("alpha") as alpha_new_binding:
        assert alpha_new_binding.snapshot_id != alpha_old_binding.snapshot_id
        assert (
            alpha_new_binding.snapshot_id
            == manager.store.get_active_snapshot("alpha").id
        )
    assert created["alpha"][0].closed is False
    await alpha_old_lease.__aexit__(None, None, None)
    alpha_snapshot = await alpha_refresh

    assert alpha_snapshot.server_id == "alpha"
    assert created["alpha"][0].closed is True
    assert manager.get_status("alpha").error_code is None
    assert beta_old_binding.snapshot_id == beta_initial.id
    await beta_old_lease.__aexit__(None, None, None)

    active_before_shutdown = manager.store.get_active_snapshot("alpha")
    shutdown_race = asyncio.create_task(
        manager.refresh_server("alpha", manager.get_status("alpha").revision)
    )
    while len(created["alpha"]) < 3:
        await asyncio.sleep(0)
    await created["alpha"][2].discovery_started.wait()
    shutdown = asyncio.create_task(manager.shutdown())
    await asyncio.sleep(0)
    created["alpha"][2].discovery_release.set()
    with pytest.raises(McpManagerError) as draining:
        await shutdown_race
    assert draining.value.code == "manager_draining"
    await shutdown
    assert created["alpha"][2].closed is True
    assert created["alpha"][2].session is None
    assert manager.store.get_active_snapshot("alpha") == active_before_shutdown
    assert manager.get_status("alpha").connection_state == "closed"
    assert manager.get_status("alpha").operation_state != "ready"
