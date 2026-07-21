from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from starter_agent.capabilities.store import CapabilityStore
from starter_agent.mcp.client import (
    ClientMetadata,
    McpClient,
    McpClientError,
    _SessionCommand,
)
from starter_agent.mcp.config import McpConfiguration, McpServerConfig
from starter_agent.mcp.manager import McpManager, McpManagerError


class _PingSession:
    def __init__(self) -> None:
        self.fail = False
        self.ping_calls = 0

    async def send_ping(self) -> object:
        self.ping_calls += 1
        if self.fail:
            raise RuntimeError("fixture ping failed")
        return object()


class _OwnerCommandClient:
    def __init__(self) -> None:
        self._session = _PingSession()
        self.session = None
        self.stderr_summary = ""
        self.command_tasks: list[asyncio.Task[object] | None] = []

    async def connect(self) -> ClientMetadata:
        self.session = self._session
        return ClientMetadata(
            protocol_version="2025-06-18",
            runtime_name="fixture-mcp",
            runtime_version="1.0.0",
            node_version="v22.1.0",
            npx_version="10.8.0",
            started_at=datetime.now(UTC),
        )

    async def run_session_command(self, command):
        self.command_tasks.append(asyncio.current_task())
        return await command(self.session)

    async def close(self) -> None:
        self.session = None


@pytest.mark.asyncio
async def test_ping_health_is_separate_from_connection_state(tmp_path: Path) -> None:
    client = _OwnerCommandClient()
    manager = McpManager(
        McpConfiguration(
            source_path=tmp_path / "mcp.json",
            servers={"alpha": McpServerConfig(command="npx")},
            config_hash="a" * 64,
        ),
        store=CapabilityStore("sqlite:///:memory:", tmp_path),
        client_factory=lambda _server_id, _config: client,
    )
    await manager.start()

    healthy = await manager.ping("alpha")
    assert healthy.connection_state == "ready"
    assert healthy.health_state == "healthy"
    assert client.session is not None
    assert client.session.ping_calls == 1

    client.session.fail = True
    with pytest.raises(McpManagerError) as raised:
        await manager.ping("alpha")
    assert raised.value.code == "ping_failed"
    unhealthy = manager.get_status("alpha")
    assert unhealthy.connection_state == "ready"
    assert unhealthy.health_state == "unhealthy"
    assert unhealthy.error_code == "ping_failed"
    assert client.session.ping_calls == 2


@pytest.mark.asyncio
async def test_owner_command_removed_during_close_is_completed() -> None:
    client = McpClient(
        McpServerConfig(command="npx"),
        initialize_timeout_seconds=1,
        close_timeout_seconds=1,
    )
    client._command_queue = asyncio.Queue()
    result = asyncio.get_running_loop().create_future()

    async def operation(_session):
        return "unexpected"

    client._command_queue.put_nowait(_SessionCommand(operation, result))
    close_signal = asyncio.Event()
    asyncio.get_running_loop().call_soon(close_signal.set)

    await client._serve_commands(object(), close_signal)

    assert result.done()
    with pytest.raises(McpClientError) as raised:
        result.result()
    assert raised.value.code == "session_closed"
