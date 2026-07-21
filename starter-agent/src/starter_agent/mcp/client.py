from __future__ import annotations

import asyncio
import os
import re
import shutil
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, TextIO

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

    def write(self, value: str) -> int:
        if not isinstance(value, str):
            raise TypeError("stderr sink accepts text only")
        self._pending += value
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            self._append(redact_runtime_text(f"{line}\n", max_chars=self.max_chars))
        if len(self._pending) > self.max_chars * 2:
            self._append(
                redact_runtime_text(self._pending, max_chars=self.max_chars)
            )
            self._pending = ""
        return len(value)

    def flush(self) -> None:
        return None

    def writable(self) -> bool:
        return True

    @property
    def summary(self) -> str:
        pending = redact_runtime_text(self._pending, max_chars=self.max_chars)
        return f"{self._tail}{pending}"[-self.max_chars :]

    def _append(self, value: str) -> None:
        self._tail = f"{self._tail}{value}"[-self.max_chars :]


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
        self._stderr = SafeStderrTail(stderr_max_chars)
        self._stack: AsyncExitStack | None = None
        self._session: Any | None = None
        self._metadata: ClientMetadata | None = None

    @property
    def session(self) -> Any | None:
        return self._session

    @property
    def metadata(self) -> ClientMetadata | None:
        return self._metadata

    @property
    def stderr_summary(self) -> str:
        return self._stderr.summary

    async def connect(self) -> ClientMetadata:
        if self._stack is not None:
            raise McpClientError("already_connected")
        candidate = AsyncExitStack()
        await candidate.__aenter__()
        started_at = datetime.now(UTC)
        stage = "preflight"
        try:
            async with asyncio.timeout(self.initialize_timeout_seconds):
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
                read_stream, write_stream = await candidate.enter_async_context(
                    self._transport_factory(parameters, self._stderr)
                )
                stage = "session"
                session = await candidate.enter_async_context(
                    self._session_factory(read_stream, write_stream)
                )
                stage = "initialize"
                result = await session.initialize()
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
        except asyncio.CancelledError:
            await self._cleanup_candidate(candidate)
            raise
        except TimeoutError as exc:
            await self._cleanup_candidate(candidate)
            raise McpClientError(
                "initialize_timeout",
                "MCP initialize exceeded its timeout",
                transport_closed=stage != "preflight",
            ) from exc
        except McpClientError:
            await self._cleanup_candidate(candidate)
            raise
        except BaseException as exc:
            await self._cleanup_candidate(candidate)
            code = "transport_error" if stage == "transport" else "initialize_error"
            raise McpClientError(
                code,
                redact_runtime_text(str(exc), max_chars=500),
                transport_closed=stage != "preflight",
            ) from exc
        self._stack = candidate
        self._session = session
        self._metadata = metadata
        return metadata

    async def close(self) -> None:
        stack = self._stack
        self._stack = None
        self._session = None
        if stack is None:
            return
        try:
            async with asyncio.timeout(self.close_timeout_seconds):
                await stack.aclose()
        except asyncio.CancelledError:
            raise
        except TimeoutError as exc:
            raise McpClientError(
                "close_timeout",
                "MCP close exceeded its timeout",
                transport_closed=True,
            ) from exc
        except BaseException as exc:
            raise McpClientError(
                "close_error",
                redact_runtime_text(str(exc), max_chars=500),
                transport_closed=True,
            ) from exc

    async def _cleanup_candidate(self, stack: AsyncExitStack) -> None:
        self._session = None
        try:
            async with asyncio.timeout(self.close_timeout_seconds):
                await stack.aclose()
        except BaseException:
            # The original connect error remains authoritative. AsyncExitStack
            # still invokes remaining callbacks when one callback fails.
            pass
