from __future__ import annotations

import asyncio
import os
import re
import shutil
import threading
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol, TextIO, TypeVar

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from starter_agent.mcp.config import (
    McpServerConfig,
    contains_high_confidence_secret,
)


_SECRET_ASSIGNMENT = re.compile(
    r"(?P<key>api[_-]?key|authorization|bearer|cookie|credential|"
    r"pass(?:word|wd)?|secret|token)(?P<separator>\s*[=:]\s*)"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\s,;]+)",
    flags=re.IGNORECASE,
)
_VERSION = re.compile(
    r"^[vV]?\d+(?:\.\d+){0,3}(?:[-+][A-Za-z0-9.-]+)?$"
)


class AsyncContextManagerFactory(Protocol):
    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...


class McpClientError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str | None = None,
        *,
        exit_code: int | None = None,
        transport_closed: bool = False,
    ) -> None:
        super().__init__(message or code)
        self.code = code
        self.exit_code = exit_code
        self.transport_closed = transport_closed


_CommandResult = TypeVar("_CommandResult")


@dataclass(slots=True)
class _SessionCommand:
    operation: Callable[[Any], Awaitable[Any]]
    result: asyncio.Future[Any]


@dataclass(frozen=True, slots=True)
class ClientMetadata:
    protocol_version: str
    runtime_name: str
    runtime_version: str
    node_version: str
    npx_version: str
    started_at: datetime
    exit_code: int | None = None
    transport_closed: bool = False


def redact_runtime_text(value: str, *, max_chars: int = 2_000) -> str:
    """Return a bounded status-safe string, never protocol input."""
    redacted = _SECRET_ASSIGNMENT.sub(
        lambda match: (
            f"{match.group('key')}{match.group('separator')}[redacted]"
        ),
        value,
    )
    if contains_high_confidence_secret(redacted):
        redacted = "[redacted]"
    redacted = "".join(
        character
        for character in redacted
        if character in "\n\r\t" or ord(character) >= 32
    )
    return redacted[-max_chars:]


class SafeStderrTail:
    """A text sink retaining only a redacted, bounded stderr tail."""

    def __init__(self, max_chars: int = 2_000) -> None:
        self.max_chars = max_chars
        self._tail = ""
        self._pending = ""
        read_fd, write_fd = os.pipe()
        self._reader = os.fdopen(read_fd, "rb", buffering=0)
        self._writer = os.fdopen(
            write_fd,
            "w",
            encoding="utf-8",
            errors="replace",
            buffering=1,
        )
        self._reader_task: asyncio.Task[None] | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._reader_task is None:
            self._reader_task = asyncio.create_task(
                asyncio.to_thread(self._drain_pipe)
            )

    def write(self, value: str) -> int:
        if not isinstance(value, str):
            raise TypeError("stderr sink accepts text only")
        return self._writer.write(value)

    def flush(self) -> None:
        self._writer.flush()

    def fileno(self) -> int:
        return self._writer.fileno()

    def writable(self) -> bool:
        return True

    async def close(self) -> None:
        if not self._writer.closed:
            self._writer.close()
        if self._reader_task is not None:
            await self._reader_task
        elif not self._reader.closed:
            self._reader.close()

    @property
    def summary(self) -> str:
        with self._lock:
            pending = redact_runtime_text(
                self._pending, max_chars=self.max_chars
            )
            return f"{self._tail}{pending}"[-self.max_chars :]

    def _append(self, value: str) -> None:
        self._tail = f"{self._tail}{value}"[-self.max_chars :]

    def _drain_pipe(self) -> None:
        try:
            while chunk := self._reader.read(4_096):
                self._ingest(chunk.decode("utf-8", errors="replace"))
        finally:
            self._reader.close()

    def _ingest(self, value: str) -> None:
        with self._lock:
            self._pending += value
            while "\n" in self._pending:
                line, self._pending = self._pending.split("\n", 1)
                self._append(
                    redact_runtime_text(
                        f"{line}\n", max_chars=self.max_chars
                    )
                )
            if len(self._pending) > self.max_chars * 2:
                self._append(
                    redact_runtime_text(
                        self._pending, max_chars=self.max_chars
                    )
                )
                self._pending = ""


def _resolve_executable(name: str) -> str | None:
    return shutil.which(name)


async def _probe_version(executable: str) -> str:
    process = await asyncio.create_subprocess_exec(
        executable,
        "--version",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with asyncio.timeout(5):
            stdout, _ = await process.communicate()
    except TimeoutError:
        process.kill()
        await process.wait()
        return "unknown"
    if process.returncode != 0:
        return "unknown"
    first_line = stdout.decode("utf-8", errors="replace").splitlines()
    return first_line[0].strip() if first_line else "unknown"


def _safe_version(value: str) -> str:
    candidate = value.strip()[:100]
    return candidate if _VERSION.fullmatch(candidate) else "unknown"


class McpClient:
    """Own one official MCP stdio transport and ClientSession lifecycle."""

    def __init__(
        self,
        config: McpServerConfig,
        *,
        initialize_timeout_seconds: float,
        close_timeout_seconds: float,
        stderr_max_chars: int = 2_000,
        executable_resolver: Callable[[str], str | None] = _resolve_executable,
        version_probe: Callable[[str], Awaitable[str]] = _probe_version,
        transport_factory: AsyncContextManagerFactory = stdio_client,
        session_factory: AsyncContextManagerFactory = ClientSession,
    ) -> None:
        self.config = config
        self.initialize_timeout_seconds = initialize_timeout_seconds
        self.close_timeout_seconds = close_timeout_seconds
        self._executable_resolver = executable_resolver
        self._version_probe = version_probe
        self._transport_factory = transport_factory
        self._session_factory = session_factory
        self._stderr_max_chars = stderr_max_chars
        self._stderr: SafeStderrTail | None = None
        self._session: Any | None = None
        self._metadata: ClientMetadata | None = None
        self._owner_task: asyncio.Task[None] | None = None
        self._ready: asyncio.Future[ClientMetadata] | None = None
        self._close_signal: asyncio.Event | None = None
        self._owner_failure: McpClientError | None = None
        self._command_queue: asyncio.Queue[_SessionCommand] | None = None
        self._lifecycle_lock = asyncio.Lock()

    @property
    def session(self) -> Any | None:
        return self._session

    @property
    def metadata(self) -> ClientMetadata | None:
        return self._metadata

    @property
    def stderr_summary(self) -> str:
        return "" if self._stderr is None else self._stderr.summary

    async def connect(self) -> ClientMetadata:
        async with self._lifecycle_lock:
            if self._session is not None and self._metadata is not None:
                return self._metadata
            self._discard_finished_owner_locked()
            if self._owner_task is None:
                loop = asyncio.get_running_loop()
                self._stderr = SafeStderrTail(self._stderr_max_chars)
                self._ready = loop.create_future()
                self._close_signal = asyncio.Event()
                self._owner_failure = None
                self._command_queue = asyncio.Queue()
                self._owner_task = asyncio.create_task(
                    self._run_lifecycle_owner(
                        self._ready,
                        self._close_signal,
                        self._stderr,
                    ),
                    name="mcp-client-lifecycle",
                )
            owner = self._owner_task
            ready = self._ready
        assert owner is not None
        assert ready is not None
        try:
            return await asyncio.shield(ready)
        except asyncio.CancelledError:
            await self._cancel_owner(owner)
            if ready.done() and not ready.cancelled():
                ready.exception()
            raise

    async def run_session_command(
        self,
        operation: Callable[[Any], Awaitable[_CommandResult]],
    ) -> _CommandResult:
        async with self._lifecycle_lock:
            queue = self._command_queue
            owner = self._owner_task
            if (
                self._session is None
                or queue is None
                or owner is None
                or owner.done()
            ):
                raise McpClientError("session_not_ready")
            result: asyncio.Future[_CommandResult] = (
                asyncio.get_running_loop().create_future()
            )
            queue.put_nowait(_SessionCommand(operation=operation, result=result))
        return await asyncio.shield(result)

    async def close(self) -> None:
        async with self._lifecycle_lock:
            owner = self._owner_task
            close_signal = self._close_signal
            if owner is None:
                return
            if close_signal is not None:
                close_signal.set()
            if self._session is None and not owner.done():
                owner.cancel()
        try:
            async with asyncio.timeout(self.close_timeout_seconds):
                await asyncio.shield(owner)
        except asyncio.CancelledError:
            await self._cancel_owner(owner)
            await self._clear_owner(owner)
            raise
        except TimeoutError as exc:
            await self._cancel_owner(owner)
            await self._clear_owner(owner)
            raise McpClientError(
                "close_timeout",
                "MCP close exceeded its timeout",
                transport_closed=True,
            ) from exc
        failure = self._owner_failure
        await self._clear_owner(owner)
        if failure is not None:
            raise failure

    async def _run_lifecycle_owner(
        self,
        ready: asyncio.Future[ClientMetadata],
        close_signal: asyncio.Event,
        stderr: SafeStderrTail,
    ) -> None:
        stage = "preflight"
        failure: McpClientError | None = None
        started_at = datetime.now(UTC)
        stderr.start()
        try:
            async with AsyncExitStack() as stack:
                try:
                    async with asyncio.timeout(
                        self.initialize_timeout_seconds
                    ):
                        node = self._executable_resolver("node")
                        if node is None:
                            raise McpClientError("node_not_found")
                        npx = self._executable_resolver("npx")
                        if npx is None:
                            raise McpClientError("npx_not_found")
                        node_version, npx_version = await asyncio.gather(
                            self._version_probe(node),
                            self._version_probe(npx),
                        )
                        environment = {
                            name: os.environ[name]
                            for name in self.config.env
                            if name in os.environ
                        }
                        parameters = StdioServerParameters(
                            command=self.config.command,
                            args=list(self.config.args),
                            cwd=self.config.cwd,
                            env=environment if self.config.env else None,
                        )
                        stage = "transport"
                        read_stream, write_stream = (
                            await stack.enter_async_context(
                                self._transport_factory(parameters, stderr)
                            )
                        )
                        stage = "session"
                        session = await stack.enter_async_context(
                            self._session_factory(read_stream, write_stream)
                        )
                        stage = "initialize"
                        result = await session.initialize()
                except TimeoutError as exc:
                    raise McpClientError(
                        "initialize_timeout",
                        "MCP initialize exceeded its timeout",
                        transport_closed=stage != "preflight",
                    ) from exc
                server_info = result.serverInfo
                metadata = ClientMetadata(
                    protocol_version=str(result.protocolVersion),
                    runtime_name=getattr(server_info, "name", None) or "unknown",
                    runtime_version=(
                        getattr(server_info, "version", None) or "unknown"
                    ),
                    node_version=_safe_version(node_version),
                    npx_version=_safe_version(npx_version),
                    started_at=started_at,
                )
                self._session = session
                self._metadata = metadata
                ready.set_result(metadata)
                stage = "ready"
                await self._serve_commands(session, close_signal)
        except asyncio.CancelledError:
            failure = McpClientError(
                "connect_closed",
                "MCP lifecycle closed before initialize completed",
                transport_closed=stage != "preflight",
            )
        except McpClientError as exc:
            failure = exc
        except BaseException as exc:
            if ready.done():
                code = "close_error"
            else:
                code = (
                    "transport_error"
                    if stage == "transport"
                    else "initialize_error"
                )
            failure = McpClientError(
                code,
                redact_runtime_text(str(exc), max_chars=500),
                transport_closed=stage != "preflight",
            )
        finally:
            self._session = None
            try:
                await stderr.close()
            except BaseException as exc:
                failure = failure or McpClientError(
                    "close_error",
                    redact_runtime_text(str(exc), max_chars=500),
                    transport_closed=True,
                )
            if failure is not None:
                if not ready.done():
                    ready.set_exception(failure)
                else:
                    self._owner_failure = failure
            elif not ready.done():
                ready.set_exception(
                    McpClientError(
                        "connect_closed",
                        transport_closed=stage != "preflight",
                    )
                )
            self._fail_pending_commands(
                failure or McpClientError("connect_closed")
            )

    async def _serve_commands(
        self,
        session: Any,
        close_signal: asyncio.Event,
    ) -> None:
        queue = self._command_queue
        assert queue is not None
        while not close_signal.is_set():
            close_waiter = asyncio.create_task(close_signal.wait())
            command_waiter = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait(
                {close_waiter, command_waiter},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if close_waiter in done:
                if command_waiter in done:
                    command = command_waiter.result()
                    if not command.result.done():
                        command.result.set_exception(
                            McpClientError("session_closed")
                        )
                else:
                    command_waiter.cancel()
                    await asyncio.gather(
                        command_waiter,
                        return_exceptions=True,
                    )
                return
            close_waiter.cancel()
            await asyncio.gather(close_waiter, return_exceptions=True)
            command = command_waiter.result()
            try:
                value = await command.operation(session)
            except asyncio.CancelledError:
                if not command.result.done():
                    command.result.set_exception(
                        McpClientError("session_closed")
                    )
                raise
            except BaseException as exc:
                if not command.result.done():
                    command.result.set_exception(exc)
            else:
                if not command.result.done():
                    command.result.set_result(value)

    def _fail_pending_commands(self, error: BaseException) -> None:
        queue = self._command_queue
        if queue is None:
            return
        while not queue.empty():
            command = queue.get_nowait()
            if not command.result.done():
                command.result.set_exception(error)

    async def _cancel_owner(self, owner: asyncio.Task[None]) -> None:
        if not owner.done():
            owner.cancel()
        try:
            async with asyncio.timeout(self.close_timeout_seconds):
                await asyncio.shield(owner)
        except (asyncio.CancelledError, TimeoutError):
            pass

    async def _clear_owner(self, owner: asyncio.Task[None]) -> None:
        async with self._lifecycle_lock:
            if self._owner_task is owner:
                self._owner_task = None
                self._ready = None
                self._close_signal = None
                self._owner_failure = None
                self._command_queue = None

    def _discard_finished_owner_locked(self) -> None:
        if self._owner_task is not None and self._owner_task.done():
            self._owner_task = None
            self._ready = None
            self._close_signal = None
            self._owner_failure = None
            self._command_queue = None
