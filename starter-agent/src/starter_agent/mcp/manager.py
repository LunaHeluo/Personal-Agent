from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from starter_agent.capabilities.models import Server
from starter_agent.capabilities.store import (
    CapabilityStore,
    RecordAlreadyExistsError,
)
from starter_agent.mcp.client import (
    ClientMetadata,
    McpClient,
    McpClientError,
    redact_runtime_text,
)
from starter_agent.mcp.config import McpConfiguration, McpServerConfig


class ManagedClient(Protocol):
    session: Any | None

    @property
    def stderr_summary(self) -> str: ...

    async def connect(self) -> ClientMetadata: ...

    async def close(self) -> None: ...


class McpManagerError(RuntimeError):
    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


@dataclass(slots=True)
class ServerHandle:
    server_id: str
    config: McpServerConfig
    client: ManagedClient
    status: Server
    connect_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    refresh_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    in_flight: int = 0
    drain_event: asyncio.Event = field(default_factory=asyncio.Event)
    accepting: bool = True

    def __post_init__(self) -> None:
        self.drain_event.set()

    @property
    def session(self) -> Any | None:
        return self.client.session


class McpManager:
    """Coordinate isolated MCP clients without a cross-server lifecycle lock."""

    def __init__(
        self,
        configuration: McpConfiguration,
        *,
        store: CapabilityStore | None = None,
        client_factory=None,
        initialize_timeout_seconds: float = 20,
        shutdown_timeout_seconds: float = 10,
    ) -> None:
        self.configuration = configuration
        self.store = store
        self.initialize_timeout_seconds = initialize_timeout_seconds
        self.shutdown_timeout_seconds = shutdown_timeout_seconds
        self._accepting = True
        if client_factory is None:
            client_factory = lambda _server_id, config: McpClient(
                config,
                initialize_timeout_seconds=initialize_timeout_seconds,
                close_timeout_seconds=shutdown_timeout_seconds,
            )
        self._handles: dict[str, ServerHandle] = {}
        for server_id, config in configuration.servers.items():
            initial = Server(
                id=server_id,
                name=server_id,
                config_source=str(configuration.source_path),
                config_hash=configuration.config_hash,
            )
            if store is not None:
                existing = store.get_server(server_id)
                if existing is None:
                    try:
                        store.create_server(initial)
                    except RecordAlreadyExistsError:
                        existing = store.get_server(server_id)
                if existing is not None:
                    initial = existing
            self._handles[server_id] = ServerHandle(
                server_id=server_id,
                config=config,
                client=client_factory(server_id, config),
                status=initial,
            )

    def get_handle(self, server_id: str) -> ServerHandle:
        try:
            return self._handles[server_id]
        except KeyError as exc:
            raise McpManagerError("server_not_found") from exc

    def get_status(self, server_id: str) -> Server:
        return self.get_handle(server_id).status

    def statuses(self) -> dict[str, Server]:
        return {
            server_id: handle.status
            for server_id, handle in self._handles.items()
        }

    async def start(self) -> dict[str, Server]:
        self._accepting = True
        for handle in self._handles.values():
            handle.accepting = True
        await asyncio.gather(
            *(self.connect(server_id) for server_id in self._handles)
        )
        return self.statuses()

    async def connect(self, server_id: str) -> Server:
        handle = self.get_handle(server_id)
        async with handle.connect_lock:
            if handle.session is not None:
                return handle.status
            started_at = datetime.now(UTC)
            self._update(
                handle,
                connection_state="connecting",
                health_state="unknown",
                operation_state="starting_candidate",
                error_code=None,
                last_error=None,
                exit_code=None,
                transport_closed=False,
                started_at=started_at,
            )
            try:
                async with asyncio.timeout(self.initialize_timeout_seconds):
                    metadata = await handle.client.connect()
            except asyncio.CancelledError:
                await self._close_after_failed_connect(handle)
                raise
            except TimeoutError:
                await self._close_after_failed_connect(handle)
                return self._record_failure(
                    handle,
                    code="initialize_timeout",
                    message="MCP connect exceeded its timeout",
                    transport_closed=True,
                )
            except McpClientError as exc:
                await self._close_after_failed_connect(handle)
                return self._record_failure(
                    handle,
                    code=exc.code,
                    message=str(exc),
                    exit_code=exc.exit_code,
                    transport_closed=exc.transport_closed,
                )
            except BaseException as exc:
                await self._close_after_failed_connect(handle)
                return self._record_failure(
                    handle,
                    code="connect_failed",
                    message=str(exc),
                    transport_closed=True,
                )
            return self._update(
                handle,
                connection_state="ready",
                health_state="healthy",
                operation_state="ready",
                protocol_version=metadata.protocol_version,
                runtime_name=metadata.runtime_name,
                runtime_version=metadata.runtime_version or "unknown",
                node_version=metadata.node_version,
                npx_version=metadata.npx_version,
                started_at=metadata.started_at,
                exit_code=metadata.exit_code,
                transport_closed=metadata.transport_closed,
                stderr_summary=handle.client.stderr_summary,
                error_code=None,
                last_error=None,
                last_checked_at=datetime.now(UTC),
            )

    async def close(self, server_id: str) -> str | None:
        handle = self.get_handle(server_id)
        handle.accepting = False
        error = await self._drain_and_close(handle)
        return error

    async def shutdown(self) -> dict[str, str]:
        self._accepting = False
        for handle in self._handles.values():
            handle.accepting = False
            if handle.status.connection_state not in {"closed", "failed"}:
                self._update(handle, operation_state="draining")
        results = await asyncio.gather(
            *(self._drain_and_close(handle) for handle in self._handles.values())
        )
        return {
            server_id: result
            for server_id, result in zip(self._handles, results, strict=True)
            if result is not None
        }

    @asynccontextmanager
    async def lease(self, server_id: str):
        handle = self.get_handle(server_id)
        if not self._accepting or not handle.accepting:
            raise McpManagerError("manager_draining")
        if handle.status.connection_state != "ready" or handle.session is None:
            raise McpManagerError("server_not_ready")
        handle.in_flight += 1
        handle.drain_event.clear()
        try:
            yield handle.session
        finally:
            handle.in_flight -= 1
            if handle.in_flight == 0:
                handle.drain_event.set()

    async def _drain_and_close(self, handle: ServerHandle) -> str | None:
        error_code: str | None = None
        try:
            async with asyncio.timeout(self.shutdown_timeout_seconds):
                await handle.drain_event.wait()
        except TimeoutError:
            error_code = "drain_timeout"
        try:
            async with asyncio.timeout(self.shutdown_timeout_seconds):
                await handle.client.close()
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            error_code = error_code or "close_timeout"
        except McpClientError as exc:
            error_code = error_code or exc.code
        except BaseException:
            error_code = error_code or "close_failed"
        self._update(
            handle,
            connection_state="closed",
            health_state="unknown",
            operation_state="idle",
            error_code=error_code,
            last_error=error_code,
            stderr_summary=handle.client.stderr_summary,
            transport_closed=True,
            last_checked_at=datetime.now(UTC),
        )
        return error_code

    async def _close_after_failed_connect(self, handle: ServerHandle) -> None:
        try:
            async with asyncio.timeout(self.shutdown_timeout_seconds):
                await handle.client.close()
        except BaseException:
            pass

    def _record_failure(
        self,
        handle: ServerHandle,
        *,
        code: str,
        message: str,
        exit_code: int | None = None,
        transport_closed: bool,
    ) -> Server:
        return self._update(
            handle,
            connection_state="failed",
            health_state="unhealthy",
            operation_state="degraded",
            error_code=code,
            last_error=redact_runtime_text(message, max_chars=500),
            exit_code=exit_code,
            transport_closed=transport_closed,
            stderr_summary=handle.client.stderr_summary,
            last_checked_at=datetime.now(UTC),
        )

    def _update(self, handle: ServerHandle, **changes: Any) -> Server:
        if self.store is None:
            handle.status = handle.status.model_copy(
                update={**changes, "revision": handle.status.revision + 1}
            )
            return handle.status
        current = self.store.get_server(handle.server_id)
        if current is None:
            handle.status = handle.status.model_copy(update=changes)
            self.store.create_server(handle.status)
            return handle.status
        handle.status = self.store.update_server(
            handle.server_id,
            expected_revision=current.revision,
            **changes,
        )
        return handle.status
