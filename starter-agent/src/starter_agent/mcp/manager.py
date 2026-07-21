from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from collections.abc import Awaitable, Callable, Iterable
from typing import Any, Protocol, TypeVar

from starter_agent.capabilities.models import Server, Snapshot
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
from starter_agent.mcp.discovery import (
    DiscoveryError,
    discover_candidate,
    discover_and_activate,
)


_CommandResult = TypeVar("_CommandResult")


class ManagedClient(Protocol):
    session: Any | None

    @property
    def stderr_summary(self) -> str: ...

    async def connect(self) -> ClientMetadata: ...

    async def close(self) -> None: ...

    async def run_session_command(
        self,
        operation: Callable[[Any], Awaitable[_CommandResult]],
    ) -> _CommandResult: ...


class McpManagerError(RuntimeError):
    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


@dataclass(slots=True)
class ClientSlot:
    client: ManagedClient
    snapshot_id: str | None = None
    in_flight: int = 0
    drain_event: asyncio.Event = field(default_factory=asyncio.Event)

    def __post_init__(self) -> None:
        self.drain_event.set()


@dataclass(slots=True)
class ServerHandle:
    server_id: str
    config: McpServerConfig
    active: ClientSlot
    status: Server
    connect_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    refresh_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    accepting: bool = True

    @property
    def client(self) -> ManagedClient:
        return self.active.client

    @property
    def in_flight(self) -> int:
        return self.active.in_flight

    @property
    def drain_event(self) -> asyncio.Event:
        return self.active.drain_event

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
        self._client_factory = client_factory
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
            active_snapshot = (
                None if store is None else store.get_active_snapshot(server_id)
            )
            self._handles[server_id] = ServerHandle(
                server_id=server_id,
                config=config,
                active=ClientSlot(
                    client=client_factory(server_id, config),
                    snapshot_id=(
                        None if active_snapshot is None else active_snapshot.id
                    ),
                ),
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

    def get_snapshot_summary(self, server_id: str) -> Snapshot | None:
        self.get_handle(server_id)
        if self.store is None:
            return None
        return self.store.get_snapshot_summary(server_id)

    async def discover(
        self,
        server_id: str,
        *,
        reserved_model_names: Iterable[str] = (),
    ) -> Snapshot:
        handle = self.get_handle(server_id)
        if self.store is None:
            raise McpManagerError("capability_store_unavailable")
        async with handle.refresh_lock:
            async with self.lease(server_id):
                self._update(handle, operation_state="discovering")
                try:
                    snapshot = await handle.client.run_session_command(
                        lambda session: discover_and_activate(
                            self.store,
                            session,
                            server_id=server_id,
                            reserved_model_names=reserved_model_names,
                        )
                    )
                except asyncio.CancelledError:
                    self._update(handle, operation_state="ready")
                    raise
                except DiscoveryError as exc:
                    self._update(
                        handle,
                        operation_state="ready",
                        error_code=exc.code,
                        last_error=exc.code,
                    )
                    raise
                except BaseException as exc:
                    self._update(
                        handle,
                        operation_state="ready",
                        error_code="discovery_failed",
                        last_error="discovery_failed",
                    )
                    raise McpManagerError("discovery_failed") from exc
                self._update(
                    handle,
                    operation_state="ready",
                    error_code=None,
                    last_error=None,
                )
                handle.active.snapshot_id = snapshot.id
                return snapshot

    async def refresh_server(
        self,
        server_id: str,
        expected_revision: int,
    ) -> Snapshot:
        handle = self.get_handle(server_id)
        if self.store is None:
            raise McpManagerError("capability_store_unavailable")
        if handle.refresh_lock.locked():
            raise McpManagerError("refresh_in_progress")
        async with handle.refresh_lock:
            persisted = self.store.get_server(server_id)
            if persisted is not None:
                handle.status = persisted
            if handle.status.revision != expected_revision:
                raise McpManagerError("revision_conflict")
            if not self._accepting or not handle.accepting:
                raise McpManagerError("manager_draining")

            old_slot = handle.active
            candidate = self._client_factory(server_id, handle.config)
            candidate_slot = ClientSlot(client=candidate)
            self._update(
                handle,
                operation_state="starting_candidate",
                error_code=None,
                last_error=None,
            )
            try:
                async with asyncio.timeout(self.initialize_timeout_seconds):
                    metadata = await candidate.connect()
                self._update(handle, operation_state="discovering")
                candidate_snapshot = await candidate.run_session_command(
                    lambda session: discover_candidate(
                        self.store,
                        session,
                        server_id=server_id,
                    )
                )
                self._update(handle, operation_state="validating_snapshot")
                activated = self.store.activate_refreshed_snapshot(
                    server_id,
                    candidate_snapshot.id,
                )
            except asyncio.CancelledError:
                await self._close_candidate(candidate)
                raise
            except TimeoutError as exc:
                await self._close_candidate(candidate)
                self._record_refresh_failure(handle, "initialize_timeout")
                raise McpManagerError("initialize_timeout") from exc
            except McpClientError as exc:
                await self._close_candidate(candidate)
                self._record_refresh_failure(handle, exc.code)
                raise McpManagerError(exc.code) from exc
            except DiscoveryError as exc:
                await self._close_candidate(candidate)
                self._record_refresh_failure(handle, exc.code)
                raise McpManagerError(exc.code) from exc
            except BaseException as exc:
                await self._close_candidate(candidate)
                self._record_refresh_failure(handle, "refresh_failed")
                raise McpManagerError("refresh_failed") from exc

            candidate_slot.snapshot_id = activated.id
            handle.active = candidate_slot
            self._update(
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
                stderr_summary=candidate.stderr_summary,
                error_code=None,
                last_error=None,
                last_checked_at=datetime.now(UTC),
            )
            drain_error = await self._drain_client_slot(old_slot)
            if drain_error is not None:
                self._update(
                    handle,
                    error_code=drain_error,
                    last_error=drain_error,
                )
            return activated

    async def ping(self, server_id: str) -> Server:
        handle = self.get_handle(server_id)
        async with handle.refresh_lock:
            async with self.lease(server_id):
                try:
                    await handle.client.run_session_command(
                        lambda session: session.send_ping()
                    )
                except asyncio.CancelledError:
                    raise
                except BaseException as exc:
                    self._update(
                        handle,
                        health_state="unhealthy",
                        error_code="ping_failed",
                        last_error="ping_failed",
                        last_checked_at=datetime.now(UTC),
                    )
                    raise McpManagerError("ping_failed") from exc
                return self._update(
                    handle,
                    health_state="healthy",
                    error_code=None,
                    last_error=None,
                    last_checked_at=datetime.now(UTC),
                )

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
            if not self._accepting or not handle.accepting:
                return handle.status
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
            if not self._accepting or not handle.accepting:
                await self._drain_and_close_locked(handle)
                return handle.status
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
        slot = handle.active
        slot.in_flight += 1
        slot.drain_event.clear()
        try:
            yield slot.client.session
        finally:
            slot.in_flight -= 1
            if slot.in_flight == 0:
                slot.drain_event.set()

    async def _drain_and_close(self, handle: ServerHandle) -> str | None:
        async with handle.connect_lock:
            return await self._drain_and_close_locked(handle)

    async def _drain_and_close_locked(
        self, handle: ServerHandle
    ) -> str | None:
        slot = handle.active
        error_code = await self._drain_client_slot(slot)
        self._update(
            handle,
            connection_state="closed",
            health_state="unknown",
            operation_state="idle",
            error_code=error_code,
            last_error=error_code,
            stderr_summary=slot.client.stderr_summary,
            transport_closed=True,
            last_checked_at=datetime.now(UTC),
        )
        return error_code

    async def _drain_client_slot(self, slot: ClientSlot) -> str | None:
        error_code: str | None = None
        try:
            async with asyncio.timeout(self.shutdown_timeout_seconds):
                await slot.drain_event.wait()
        except TimeoutError:
            error_code = "drain_timeout"
        try:
            async with asyncio.timeout(self.shutdown_timeout_seconds):
                await slot.client.close()
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            error_code = error_code or "close_timeout"
        except McpClientError as exc:
            error_code = error_code or exc.code
        except BaseException:
            error_code = error_code or "close_failed"
        return error_code

    async def _close_candidate(self, candidate: ManagedClient) -> None:
        try:
            async with asyncio.timeout(self.shutdown_timeout_seconds):
                await candidate.close()
        except BaseException:
            pass

    def _record_refresh_failure(
        self,
        handle: ServerHandle,
        code: str,
    ) -> None:
        assert self.store is not None
        safe_code = redact_runtime_text(code, max_chars=100)
        self.store.mark_active_snapshot_stale(
            handle.server_id,
            error=safe_code,
        )
        self._update(
            handle,
            operation_state="degraded",
            error_code=safe_code,
            last_error=safe_code,
            last_checked_at=datetime.now(UTC),
        )

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
