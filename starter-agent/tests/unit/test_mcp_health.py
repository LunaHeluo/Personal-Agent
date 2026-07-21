from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from mcp.types import Implementation, InitializeResult, ServerCapabilities

from starter_agent.capabilities.store import CapabilityStore
from starter_agent.mcp.client import (
    ClientMetadata,
    McpClient,
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


class _RecordingTransport:
    async def __aenter__(self):
        return object(), object()

    async def __aexit__(self, *_exc_info) -> None:
        return None


class _RecordingSession:
    def __init__(self) -> None:
        self.operation_task: asyncio.Task[object] | None = None

    async def initialize(self) -> InitializeResult:
        return InitializeResult(
            protocolVersion="2025-06-18",
            capabilities=ServerCapabilities(),
            serverInfo=Implementation(name="fixture-mcp", version="1.0.0"),
        )

    async def send_ping(self) -> object:
        self.operation_task = asyncio.current_task()
        return object()


class _RecordingSessionContext:
    def __init__(self, session: _RecordingSession) -> None:
        self.session = session
        self.owner_task: asyncio.Task[object] | None = None

    async def __aenter__(self) -> _RecordingSession:
        self.owner_task = asyncio.current_task()
        return self.session

    async def __aexit__(self, *_exc_info) -> None:
        return None


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
async def test_manager_ping_runs_on_real_client_lifecycle_owner_task(
    tmp_path: Path,
) -> None:
    session = _RecordingSession()
    session_context = _RecordingSessionContext(session)
    client = McpClient(
        McpServerConfig(command="npx"),
        initialize_timeout_seconds=1,
        close_timeout_seconds=1,
        executable_resolver=lambda name: f"C:/runtime/{name}",
        version_probe=lambda _executable: _async_value("1.0.0"),
        transport_factory=lambda _parameters, _stderr: _RecordingTransport(),
        session_factory=lambda _read, _write: session_context,
    )
    manager = McpManager(
        McpConfiguration(
            source_path=tmp_path / "mcp.json",
            servers={"alpha": McpServerConfig(command="npx")},
            config_hash="a" * 64,
        ),
        store=CapabilityStore("sqlite:///:memory:", tmp_path),
        client_factory=lambda _server_id, _config: client,
    )
    caller_task = asyncio.current_task()

    await manager.start()
    await manager.ping("alpha")

    assert session_context.owner_task is not None
    assert session.operation_task is session_context.owner_task
    assert session.operation_task is not caller_task
    await manager.shutdown()


async def _async_value(value: str) -> str:
    return value
