import asyncio
from pathlib import Path
import sys

import pytest
from mcp import StdioServerParameters
from mcp.types import Implementation, InitializeResult, ServerCapabilities

from starter_agent.mcp.client import McpClient, McpClientError
from starter_agent.mcp.config import McpServerConfig


FIXTURE_SERVER = (
    Path(__file__).resolve().parents[1] / "fixtures" / "mcp" / "stdio_server.py"
)


class _Transport:
    def __init__(self, stderr, *, stderr_text: str = "") -> None:
        self.stderr = stderr
        self.stderr_text = stderr_text
        self.entered = False
        self.exited = False
        self.read_stream = object()
        self.write_stream = object()

    async def __aenter__(self):
        self.entered = True
        if self.stderr_text:
            self.stderr.write(self.stderr_text)
        return self.read_stream, self.write_stream

    async def __aexit__(self, *_exc_info) -> None:
        self.exited = True


class _Session:
    def __init__(self, result: InitializeResult | None = None) -> None:
        self.result = result
        self.initialize_started = asyncio.Event()
        self.release_initialize = asyncio.Event()

    async def initialize(self) -> InitializeResult:
        self.initialize_started.set()
        if self.result is None:
            await self.release_initialize.wait()
            raise AssertionError("unreachable")
        return self.result


class _SessionContext:
    def __init__(self, session: _Session) -> None:
        self.session = session
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> _Session:
        self.entered = True
        return self.session

    async def __aexit__(self, *_exc_info) -> None:
        self.exited = True


def _resolver(name: str) -> str:
    return str(Path("C:/runtime") / name)


async def _version_probe(executable: str) -> str:
    return "v22.1.0" if Path(executable).name == "node" else "10.8.0"


def _initialize_result(version: str = "9.4.1") -> InitializeResult:
    return InitializeResult(
        protocolVersion="2025-06-18",
        capabilities=ServerCapabilities(),
        serverInfo=Implementation(name="fixture-mcp", version=version),
    )


@pytest.mark.asyncio
async def test_connect_uses_official_stdio_session_and_runtime_metadata() -> None:
    captured: dict[str, object] = {}
    session = _Session(_initialize_result())
    session_context = _SessionContext(session)

    def transport_factory(parameters, stderr):
        captured["parameters"] = parameters
        captured["stderr"] = stderr
        transport = _Transport(stderr)
        captured["transport"] = transport
        return transport

    def session_factory(read_stream, write_stream):
        captured["streams"] = (read_stream, write_stream)
        return session_context

    client = McpClient(
        McpServerConfig(command="npx", args=("@playwright/mcp@latest",)),
        initialize_timeout_seconds=0.2,
        close_timeout_seconds=0.2,
        executable_resolver=_resolver,
        version_probe=_version_probe,
        transport_factory=transport_factory,
        session_factory=session_factory,
    )

    metadata = await client.connect()

    parameters = captured["parameters"]
    transport = captured["transport"]
    assert isinstance(parameters, StdioServerParameters)
    assert parameters.command == "npx"
    assert parameters.args == ["@playwright/mcp@latest"]
    assert captured["streams"] == (transport.read_stream, transport.write_stream)
    assert client.session is session
    assert metadata.protocol_version == "2025-06-18"
    assert metadata.runtime_name == "fixture-mcp"
    assert metadata.runtime_version == "9.4.1"
    assert metadata.node_version == "v22.1.0"
    assert metadata.npx_version == "10.8.0"
    assert metadata.exit_code is None

    await client.close()

    assert session_context.exited is True
    assert transport.exited is True
    assert client.session is None


@pytest.mark.asyncio
async def test_initialize_timeout_cleans_transport_and_redacts_stderr() -> None:
    session = _Session()
    session_context = _SessionContext(session)
    captured: dict[str, _Transport] = {}

    def transport_factory(_parameters, stderr):
        transport = _Transport(
            stderr,
            stderr_text=(
                "starting fixture\n"
                "token=never-store-this\n"
                f"credential=ghp_{'A' * 36}\n"
            ),
        )
        captured["transport"] = transport
        return transport

    client = McpClient(
        McpServerConfig(command="npx", args=("@playwright/mcp@latest",)),
        initialize_timeout_seconds=0.1,
        close_timeout_seconds=0.2,
        executable_resolver=_resolver,
        version_probe=_version_probe,
        transport_factory=transport_factory,
        session_factory=lambda _read, _write: session_context,
    )

    with pytest.raises(McpClientError) as raised:
        await client.connect()

    assert raised.value.code == "initialize_timeout"
    assert session_context.exited is True
    assert captured["transport"].exited is True
    assert client.session is None
    assert "never-store-this" not in client.stderr_summary
    assert "ghp_" not in client.stderr_summary
    assert "[redacted]" in client.stderr_summary.lower()


@pytest.mark.asyncio
async def test_connect_cancellation_propagates_after_cleanup() -> None:
    session = _Session()
    session_context = _SessionContext(session)
    captured: dict[str, _Transport] = {}

    def transport_factory(_parameters, stderr):
        captured["transport"] = _Transport(stderr)
        return captured["transport"]

    client = McpClient(
        McpServerConfig(command="npx"),
        initialize_timeout_seconds=1,
        close_timeout_seconds=0.2,
        executable_resolver=_resolver,
        version_probe=_version_probe,
        transport_factory=transport_factory,
        session_factory=lambda _read, _write: session_context,
    )
    task = asyncio.create_task(client.connect())
    await session.initialize_started.wait()

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert session_context.exited is True
    assert captured["transport"].exited is True
    assert client.session is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("missing", "expected_code"),
    (("node", "node_not_found"), ("npx", "npx_not_found")),
)
async def test_missing_runtime_has_stable_error_code(
    missing: str, expected_code: str
) -> None:
    def resolver(name: str) -> str | None:
        return None if name == missing else _resolver(name)

    client = McpClient(
        McpServerConfig(command="npx"),
        initialize_timeout_seconds=0.2,
        close_timeout_seconds=0.2,
        executable_resolver=resolver,
        version_probe=_version_probe,
    )

    with pytest.raises(McpClientError) as raised:
        await client.connect()

    assert raised.value.code == expected_code


@pytest.mark.asyncio
async def test_real_sdk_stdio_lifecycle_closes_in_its_owner_task() -> None:
    client = McpClient(
        McpServerConfig(command=sys.executable, args=(str(FIXTURE_SERVER),)),
        initialize_timeout_seconds=10,
        close_timeout_seconds=5,
        executable_resolver=_resolver,
        version_probe=_version_probe,
    )

    metadata = await asyncio.wait_for(
        asyncio.create_task(client.connect()), timeout=12
    )

    assert metadata.runtime_name == "fixture-mcp"
    assert client.session is not None
    await asyncio.wait_for(asyncio.create_task(client.close()), timeout=7)
    assert client.session is None
