import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from starter_agent.capabilities.store import CapabilityStore
from starter_agent.interfaces import api as api_module
from starter_agent.mcp.client import ClientMetadata, McpClientError
from starter_agent.mcp.config import McpConfiguration, McpServerConfig
from starter_agent.mcp.manager import McpManager, McpManagerError


class _ManagedClient:
    def __init__(
        self,
        server_id: str,
        *,
        connect_error: McpClientError | None = None,
    ) -> None:
        self.server_id = server_id
        self.connect_error = connect_error
        self._session = object()
        self.session = None
        self.closed = False
        self.connect_count = 0
        self.stderr_summary = "fixture stderr"

    async def connect(self) -> ClientMetadata:
        self.connect_count += 1
        if self.connect_error is not None:
            raise self.connect_error
        self.session = self._session
        return ClientMetadata(
            protocol_version="2025-06-18",
            runtime_name=f"fixture-{self.server_id}",
            runtime_version=f"1.0.{self.connect_count}",
            node_version="v22.1.0",
            npx_version="10.8.0",
            started_at=datetime.now(UTC),
        )

    async def close(self) -> None:
        self.closed = True
        self.session = None


class _BlockingManagedClient(_ManagedClient):
    def __init__(self, server_id: str) -> None:
        super().__init__(server_id)
        self.connect_started = asyncio.Event()
        self.release_connect = asyncio.Event()

    async def connect(self) -> ClientMetadata:
        self.connect_count += 1
        self.connect_started.set()
        await self.release_connect.wait()
        self.session = self._session
        return ClientMetadata(
            protocol_version="2025-06-18",
            runtime_name=f"fixture-{self.server_id}",
            runtime_version="1.0.1",
            node_version="v22.1.0",
            npx_version="10.8.0",
            started_at=datetime.now(UTC),
        )


def _configuration(tmp_path: Path) -> McpConfiguration:
    return McpConfiguration(
        source_path=tmp_path / "mcp.json",
        servers={
            "alpha": McpServerConfig(command="npx", args=("alpha",)),
            "beta": McpServerConfig(command="npx", args=("beta",)),
        },
        config_hash="a" * 64,
    )


def _manager(tmp_path: Path, **kwargs):
    clients: dict[str, _ManagedClient] = {}

    def factory(server_id: str, _config: McpServerConfig):
        client = _ManagedClient(server_id)
        clients[server_id] = client
        return client

    manager = McpManager(
        _configuration(tmp_path),
        store=CapabilityStore("sqlite:///:memory:", tmp_path),
        client_factory=factory,
        initialize_timeout_seconds=0.2,
        shutdown_timeout_seconds=kwargs.get("shutdown_timeout_seconds", 0.2),
    )
    return manager, clients


@pytest.mark.asyncio
async def test_servers_have_isolated_sessions_locks_and_lifecycles(
    tmp_path: Path,
) -> None:
    manager, clients = _manager(tmp_path)

    await manager.start()

    alpha = manager.get_handle("alpha")
    beta = manager.get_handle("beta")
    assert alpha.session is not beta.session
    assert alpha.connect_lock is not beta.connect_lock
    assert alpha.refresh_lock is not beta.refresh_lock
    assert manager.get_status("alpha").runtime_version == "1.0.1"
    assert manager.get_status("beta").runtime_name == "fixture-beta"

    await manager.close("alpha")

    assert clients["alpha"].closed is True
    assert clients["beta"].closed is False
    assert manager.get_status("alpha").connection_state == "closed"
    assert manager.get_status("beta").connection_state == "ready"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_shutdown_rejects_new_leases_drains_and_closes_all(
    tmp_path: Path,
) -> None:
    manager, clients = _manager(tmp_path)
    await manager.start()
    lease = manager.lease("alpha")
    assert await lease.__aenter__() is clients["alpha"].session

    shutdown = asyncio.create_task(manager.shutdown())
    await asyncio.sleep(0)
    with pytest.raises(McpManagerError) as raised:
        async with manager.lease("beta"):
            pass
    assert raised.value.code == "manager_draining"
    assert clients["alpha"].closed is False

    await lease.__aexit__(None, None, None)
    errors = await shutdown

    assert errors == {}
    assert all(client.closed for client in clients.values())


@pytest.mark.asyncio
async def test_shutdown_drain_timeout_is_stable_and_does_not_block_other_server(
    tmp_path: Path,
) -> None:
    manager, clients = _manager(tmp_path, shutdown_timeout_seconds=0.01)
    await manager.start()
    lease = manager.lease("alpha")
    await lease.__aenter__()

    errors = await manager.shutdown()

    assert errors == {"alpha": "drain_timeout"}
    assert clients["alpha"].closed is True
    assert clients["beta"].closed is True
    assert manager.get_status("alpha").error_code == "drain_timeout"
    await lease.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_abnormal_server_failure_is_isolated_and_records_known_exit_code(
    tmp_path: Path,
) -> None:
    clients: dict[str, _ManagedClient] = {}

    def factory(server_id: str, _config: McpServerConfig):
        error = (
            McpClientError(
                "transport_closed",
                "token=do-not-store",
                exit_code=17,
                transport_closed=True,
            )
            if server_id == "alpha"
            else None
        )
        clients[server_id] = _ManagedClient(server_id, connect_error=error)
        return clients[server_id]

    manager = McpManager(
        _configuration(tmp_path),
        client_factory=factory,
        initialize_timeout_seconds=0.2,
        shutdown_timeout_seconds=0.2,
    )

    await manager.start()

    failed = manager.get_status("alpha")
    assert failed.connection_state == "failed"
    assert failed.error_code == "transport_closed"
    assert failed.exit_code == 17
    assert failed.transport_closed is True
    assert "do-not-store" not in (failed.last_error or "")
    assert manager.get_status("beta").connection_state == "ready"
    await manager.shutdown()


def test_api_lifespan_keeps_base_agent_available_when_mcp_start_fails(
    monkeypatch,
) -> None:
    class FailingManager:
        def __init__(self) -> None:
            self.shutdown_called = False

        async def start(self) -> None:
            raise RuntimeError("fixture MCP startup failed")

        async def shutdown(self) -> dict[str, str]:
            self.shutdown_called = True
            return {}

    manager = FailingManager()
    monkeypatch.setattr(api_module, "create_mcp_manager", lambda: manager)

    with TestClient(api_module.create_api()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert manager.shutdown_called is True


def test_api_lifespan_uses_controlled_manager_in_test_suite(
    mcp_test_manager,
) -> None:
    with TestClient(api_module.create_api()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert mcp_test_manager.start_count == 1
    assert mcp_test_manager.shutdown_count == 1


@pytest.mark.asyncio
async def test_close_serializes_with_connect_and_cannot_bounce_back_ready(
    tmp_path: Path,
) -> None:
    clients: dict[str, _ManagedClient] = {}

    def factory(server_id: str, _config: McpServerConfig):
        client = (
            _BlockingManagedClient(server_id)
            if server_id == "alpha"
            else _ManagedClient(server_id)
        )
        clients[server_id] = client
        return client

    manager = McpManager(
        _configuration(tmp_path),
        client_factory=factory,
        initialize_timeout_seconds=1,
        shutdown_timeout_seconds=0.2,
    )
    alpha = clients["alpha"]
    assert isinstance(alpha, _BlockingManagedClient)
    connect_task = asyncio.create_task(manager.connect("alpha"))
    await alpha.connect_started.wait()

    close_task = asyncio.create_task(manager.close("alpha"))
    await asyncio.sleep(0)
    close_finished_while_connecting = close_task.done()
    alpha.release_connect.set()
    await asyncio.gather(connect_task, close_task)
    raced_state = manager.get_status("alpha").connection_state
    raced_session = alpha.session

    await manager.close("alpha")

    assert close_finished_while_connecting is False
    assert raced_state == "closed"
    assert raced_session is None
    with pytest.raises(McpManagerError) as raised:
        async with manager.lease("alpha"):
            pass
    assert raised.value.code == "manager_draining"
