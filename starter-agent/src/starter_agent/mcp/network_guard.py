from __future__ import annotations

import asyncio
import inspect
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable, Iterable
from typing import Any
from urllib.parse import urlsplit

from starter_agent.capabilities.gate import NetworkGuardAttestation
from starter_agent.capabilities.policy import extract_url_targets
from starter_agent.mcp.config import McpServerConfig


IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str], Iterable[IPAddress] | Awaitable[Iterable[IPAddress]]]

_MAX_PROXY_HEADER_BYTES = 16_384
_MAX_HTTP_RESPONSE_BYTES = 8 * 1024 * 1024
_MAX_CONTROL_BODY_BYTES = 64 * 1024
_CONTROL_API_PREFIX = "/v1/capabilities/"
_HTTP_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_HTTP_READ_METHODS = frozenset({"GET", "HEAD"})
_CONTROL_METHODS = frozenset({"GET", "HEAD", "POST", "DELETE"})
_HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
_FORBIDDEN_HOST_SUFFIXES = (
    ".home",
    ".internal",
    ".lan",
    ".local",
    ".localdomain",
    ".localhost",
)
_UNSAFE_PLAYWRIGHT_OPTIONS = frozenset(
    {
        "--allow-unrestricted-file-access",
        "--cdp-endpoint",
        "--extension",
        "--ignore-https-errors",
        "--no-sandbox",
        "--storage-state",
        "--user-data-dir",
    }
)
_TARGETLESS_READ_TOOLS = frozenset(
    {
        "browser_snapshot",
        "mcp__playwright__browser_snapshot",
        "browser_click",
        "mcp__playwright__browser_click",
    }
)
_NAVIGATION_TOOLS = frozenset(
    {"browser_navigate", "mcp__playwright__browser_navigate"}
)


async def _default_resolver(hostname: str) -> list[IPAddress]:
    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(
        hostname,
        443,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM,
    )
    return list(
        dict.fromkeys(
            ipaddress.ip_address(record[4][0])
            for record in records
        )
    )


class PlaywrightNetworkGuard:
    """Route Playwright HTTPS traffic through a deny-private CONNECT proxy."""

    def __init__(
        self,
        *,
        resolver: Resolver = _default_resolver,
        connect_timeout_seconds: float = 15,
        control_origins: Iterable[str] = (),
    ) -> None:
        self._resolver = resolver
        self._connect_timeout_seconds = connect_timeout_seconds
        self._control_origins = frozenset(
            self._validate_control_origin(origin)
            for origin in control_origins
        )
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(socket.SOMAXCONN)
        self._socket.setblocking(False)
        self._address = self._socket.getsockname()
        self._server: asyncio.AbstractServer | None = None
        self._connection_tasks: set[asyncio.Task[None]] = set()
        self._disposed = False
        self._connection_sequence = 0
        self._connection_history: list[tuple[int, str]] = []
        self._authorized_targets: dict[
            tuple[str, str, str, int], tuple[int, tuple[str, ...]]
        ] = {}
        self._active_generations: dict[tuple[str, str], int] = {}
        self._pending_navigations: dict[
            tuple[str, str, str, str],
            tuple[int, tuple[str, ...], int, int | None],
        ] = {}
        self._latest_navigation_sequence: dict[
            tuple[str, str, int], int
        ] = {}
        self._navigation_sequence = 0
        self._last_proxy_error: str | None = None

    @property
    def address(self) -> tuple[str, int]:
        return self._address[0], int(self._address[1])

    @property
    def proxy_url(self) -> str:
        host, port = self.address
        return f"http://{host}:{port}"

    @property
    def connection_targets(self) -> tuple[str, ...]:
        return tuple(target for _sequence, target in self._connection_history)

    @property
    def last_proxy_error(self) -> str | None:
        return self._last_proxy_error

    async def start(self) -> None:
        if self._disposed:
            raise RuntimeError("browser_network_guard_closed")
        if self._server is None:
            self._server = await asyncio.start_server(
                self._accept_connection,
                sock=self._socket,
            )

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            tasks = tuple(self._connection_tasks)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await self._server.wait_closed()
            self._server = None
        elif not self._disposed:
            self._socket.close()
        self._disposed = True
        self._authorized_targets.clear()
        self._active_generations.clear()
        self._pending_navigations.clear()
        self._latest_navigation_sequence.clear()
        self._connection_history.clear()

    def _accept_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        task = asyncio.create_task(self._handle_connection(reader, writer))
        self._connection_tasks.add(task)
        task.add_done_callback(self._connection_done)

    def _connection_done(self, task: asyncio.Task[None]) -> None:
        self._connection_tasks.discard(task)
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                traceback = error.__traceback__
                while traceback is not None and traceback.tb_next is not None:
                    traceback = traceback.tb_next
                location = (
                    "unknown"
                    if traceback is None
                    else f"{traceback.tb_frame.f_code.co_name}:{traceback.tb_lineno}"
                )
                self._last_proxy_error = f"{type(error).__name__}:{location}"

    def dispose(self) -> None:
        if self._server is not None:
            raise RuntimeError("browser_network_guard_running")
        if not self._disposed:
            self._socket.close()
            self._disposed = True

    async def __call__(self, request: Any) -> NetworkGuardAttestation:
        if self._server is None:
            raise RuntimeError("browser_network_guard_not_started")
        targets = extract_url_targets(request.arguments)
        if not targets:
            tool_name = str(getattr(request, "tool_name", ""))
            binding = self._request_binding(request)
            if tool_name not in _TARGETLESS_READ_TOOLS or binding is None:
                raise RuntimeError("browser_network_target_required")
            generation = self._active_generations.get(
                (binding[0], binding[2])
            )
            committed = (
                None
                if generation is None
                else self._authorized_targets.get((*binding, generation))
            )
            if committed is None:
                raise RuntimeError("browser_network_target_required")
            targets = committed[1]
        peer_verified = True
        for target in targets:
            scheme = await self._validate_web_target(target)
            peer_verified = peer_verified and scheme == "https"
        binding = self._request_binding(request)
        tool_name = str(getattr(request, "tool_name", ""))
        call_id = str(getattr(request, "call_id", "")).strip()
        if binding is not None and call_id and tool_name in _NAVIGATION_TOOLS:
            self._navigation_sequence += 1
            generation = self._active_generations.get(
                (binding[0], binding[2])
            )
            if generation is not None:
                state_key = (binding[0], binding[2], generation)
                self._authorized_targets = {
                    key: value
                    for key, value in self._authorized_targets.items()
                    if not (
                        key[0] == binding[0]
                        and key[2] == binding[2]
                        and key[3] == generation
                    )
                }
                self._latest_navigation_sequence[state_key] = (
                    self._navigation_sequence
                )
            self._pending_navigations[(*binding, call_id)] = (
                self._navigation_sequence,
                targets,
                self._connection_sequence,
                generation,
            )
        return NetworkGuardAttestation(
            targets=targets,
            dns_pinned=True,
            redirects_enforced=True,
            peer_verified=peer_verified,
        )

    def activate_generation(
        self,
        server_id: str,
        snapshot_id: str,
        generation: int,
    ) -> None:
        key = (server_id, snapshot_id)
        if self._active_generations.get(key) == generation:
            return
        self.invalidate_server(server_id)
        self._active_generations[key] = generation

    def invalidate_server(self, server_id: str) -> None:
        self._active_generations = {
            key: value
            for key, value in self._active_generations.items()
            if key[0] != server_id
        }
        self._authorized_targets = {
            key: value
            for key, value in self._authorized_targets.items()
            if key[0] != server_id
        }
        self._pending_navigations = {
            key: value
            for key, value in self._pending_navigations.items()
            if key[0] != server_id
        }
        self._latest_navigation_sequence = {
            key: value
            for key, value in self._latest_navigation_sequence.items()
            if key[0] != server_id
        }

    async def commit_navigation(
        self,
        request: Any,
        *,
        lease_generation: int,
        final_url: str | None,
    ) -> bool:
        binding = self._request_binding(request)
        call_id = str(getattr(request, "call_id", "")).strip()
        if binding is None or not call_id:
            return False
        pending = self._pending_navigations.pop((*binding, call_id), None)
        if pending is None:
            return False
        if not final_url:
            return False
        sequence, requested_targets, cursor, staged_generation = pending
        if staged_generation != lease_generation or (
            self._active_generations.get((binding[0], binding[2]))
            != lease_generation
        ):
            return False
        try:
            await self._validate_web_target(final_url)
        except (TypeError, ValueError):
            return False
        parsed = urlsplit(final_url)
        port = parsed.port or (
            443 if parsed.scheme.casefold() == "https" else 80
        )
        authority = f"{parsed.hostname}:{port}"
        connected_during_navigation = any(
            connection_sequence > cursor and target == authority
            for connection_sequence, target in self._connection_history
        )
        reused_exact_connection = (
            final_url in requested_targets
            and any(
                target == authority
                for _connection_sequence, target in self._connection_history
            )
        )
        if not connected_during_navigation and not reused_exact_connection:
            return False
        targets = (final_url,)
        key = (*binding, lease_generation)
        navigation_key = (binding[0], binding[2], lease_generation)
        if self._latest_navigation_sequence.get(navigation_key) != sequence:
            return False
        previous = self._authorized_targets.get(key)
        if previous is not None and previous[0] > sequence:
            return False
        self._authorized_targets[key] = (sequence, targets)
        return True

    def discard_navigation(self, request: Any) -> None:
        binding = self._request_binding(request)
        call_id = str(getattr(request, "call_id", "")).strip()
        if binding is not None and call_id:
            self._pending_navigations.pop((*binding, call_id), None)

    @staticmethod
    def _request_binding(request: Any) -> tuple[str, str, str] | None:
        values = tuple(
            str(getattr(request, field, "")).strip()
            for field in ("server_id", "session_id", "snapshot_id")
        )
        return values if all(values) else None

    def secure_config(self, config: McpServerConfig) -> McpServerConfig:
        args = list(config.args)
        if any(argument in _UNSAFE_PLAYWRIGHT_OPTIONS for argument in args):
            raise ValueError("unsafe_playwright_launch_option")
        args.extend(
            [
                "--isolated",
                "--headless",
                "--block-service-workers",
                "--proxy-server",
                self.proxy_url,
                "--proxy-bypass",
                "<-loopback>",
            ]
        )
        return config.model_copy(update={"args": tuple(args)})

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        upstream_writer: asyncio.StreamWriter | None = None
        response_sent = False
        try:
            header = await asyncio.wait_for(
                reader.readuntil(b"\r\n\r\n"),
                timeout=10,
            )
            if len(header) > _MAX_PROXY_HEADER_BYTES:
                raise ValueError("proxy_header_too_large")
            first_line = header.split(b"\r\n", 1)[0].decode(
                "ascii",
                errors="strict",
            )
            method, authority, version = first_line.split(" ", 2)
            if method == "CONNECT":
                hostname, port = self._parse_authority(authority)
                upstream_request = None
            else:
                upstream_request, hostname, port = self._prepare_http_request(
                    header, self._control_origins
                )
            if self._is_control_endpoint("http", hostname, port):
                addresses = (ipaddress.ip_address(hostname),)
            else:
                addresses = await self._public_addresses(hostname)
            last_error: OSError | None = None
            upstream_reader: asyncio.StreamReader | None = None
            for address in addresses:
                try:
                    upstream_reader, upstream_writer = await asyncio.wait_for(
                        asyncio.open_connection(str(address), port),
                        timeout=self._connect_timeout_seconds,
                    )
                    break
                except OSError as exc:
                    last_error = exc
            if upstream_reader is None or upstream_writer is None:
                raise last_error or OSError("public_target_unreachable")
            self._record_connection_target(hostname, port)
            if upstream_request is None:
                writer.write(
                    b"HTTP/1.1 200 Connection Established\r\n"
                    b"Connection: keep-alive\r\n\r\n"
                )
                await writer.drain()
                response_sent = True
                await asyncio.gather(
                    self._pipe(reader, upstream_writer),
                    self._pipe(upstream_reader, writer),
                )
            else:
                upstream_writer.write(upstream_request)
                body_length = self._forwarded_body_length(upstream_request)
                if body_length:
                    body = await asyncio.wait_for(
                        reader.readexactly(body_length),
                        timeout=self._connect_timeout_seconds,
                    )
                    upstream_writer.write(body)
                await upstream_writer.drain()
                response_sent = True
                await self._copy_limited_http_response(
                    upstream_reader,
                    writer,
                )
        except (
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            TimeoutError,
            UnicodeError,
            ValueError,
        ) as exc:
            self._last_proxy_error = type(exc).__name__
            if not response_sent:
                writer.write(
                    b"HTTP/1.1 403 Forbidden\r\n"
                    b"Connection: close\r\n"
                    b"Content-Length: 0\r\n\r\n"
                )
                await writer.drain()
        except OSError as exc:
            self._last_proxy_error = type(exc).__name__
            if not response_sent:
                writer.write(
                    b"HTTP/1.1 502 Bad Gateway\r\n"
                    b"Connection: close\r\n"
                    b"Content-Length: 0\r\n\r\n"
                )
                await writer.drain()
        finally:
            if upstream_writer is not None:
                upstream_writer.close()
                await upstream_writer.wait_closed()
            writer.close()
            await writer.wait_closed()

    async def _validate_web_target(self, value: str) -> str:
        parsed = urlsplit(value)
        scheme = parsed.scheme.casefold()
        if (
            parsed.hostname is not None
            and parsed.port is not None
            and self._is_control_endpoint(scheme, parsed.hostname, parsed.port)
            and parsed.username is None
            and parsed.password is None
        ):
            return scheme
        expected_port = 443 if scheme == "https" else 80
        if (
            scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or (parsed.port is not None and parsed.port != expected_port)
        ):
            raise ValueError("browser_web_target_required")
        await self._public_addresses(parsed.hostname)
        return scheme

    @staticmethod
    def _validate_control_origin(origin: str) -> tuple[str, str, int]:
        parsed = urlsplit(origin)
        try:
            address = (
                None
                if parsed.hostname is None
                else ipaddress.ip_address(parsed.hostname)
            )
            port = parsed.port
        except ValueError as exc:
            raise ValueError("control_origin_invalid") from exc
        if (
            parsed.scheme.casefold() != "http"
            or address is None
            or not address.is_loopback
            or str(address) not in {"127.0.0.1", "::1"}
            or port is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("control_origin_invalid")
        return "http", str(address), port

    def _is_control_endpoint(
        self, scheme: str, hostname: str, port: int
    ) -> bool:
        try:
            normalized = str(ipaddress.ip_address(hostname))
        except ValueError:
            return False
        return (scheme.casefold(), normalized, port) in self._control_origins

    async def _public_addresses(self, hostname: str) -> tuple[IPAddress, ...]:
        normalized = hostname.rstrip(".").casefold()
        if (
            normalized == "localhost"
            or normalized.endswith(_FORBIDDEN_HOST_SUFFIXES)
        ):
            raise ValueError("browser_private_target")
        try:
            literal = ipaddress.ip_address(normalized)
        except ValueError:
            resolved = self._resolver(normalized)
            if inspect.isawaitable(resolved):
                resolved = await resolved
            addresses = tuple(resolved)
        else:
            addresses = (literal,)
        if not addresses or any(not address.is_global for address in addresses):
            raise ValueError("browser_private_target")
        return addresses

    def _record_connection_target(self, hostname: str, port: int) -> None:
        self._connection_sequence += 1
        self._connection_history.append(
            (self._connection_sequence, f"{hostname}:{port}")
        )

    @staticmethod
    def _parse_authority(authority: str) -> tuple[str, int]:
        parsed = urlsplit(f"//{authority}")
        if (
            not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port != 443
        ):
            raise ValueError("https_connect_required")
        return parsed.hostname, parsed.port

    @staticmethod
    def _prepare_http_request(
        header: bytes,
        control_origins: frozenset[tuple[str, str, int]] = frozenset(),
    ) -> tuple[bytes, str, int]:
        lines = header[:-4].split(b"\r\n")
        method_raw, target_raw, version = lines[0].decode(
            "ascii",
            errors="strict",
        ).split(" ", 2)
        method = method_raw.upper()
        if version not in {"HTTP/1.0", "HTTP/1.1"}:
            raise ValueError("http_read_method_required")
        parsed = urlsplit(target_raw)
        port = parsed.port or 80
        try:
            normalized_host = str(ipaddress.ip_address(parsed.hostname))
        except ValueError:
            normalized_host = parsed.hostname.casefold()
        is_control = (
            "http", normalized_host, port
        ) in control_origins
        allowed_methods = _CONTROL_METHODS if is_control else _HTTP_READ_METHODS
        if method not in allowed_methods:
            raise ValueError("http_read_method_required")
        if (
            parsed.scheme.casefold() != "http"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or (port != 80 and not is_control)
        ):
            raise ValueError("http_absolute_public_target_required")

        parsed_headers: list[tuple[str, str]] = []
        connection_tokens: set[str] = set()
        for raw_line in lines[1:]:
            if (
                not raw_line
                or raw_line.startswith((b" ", b"\t"))
                or b":" not in raw_line
            ):
                raise ValueError("invalid_proxy_header")
            raw_name, raw_value = raw_line.split(b":", 1)
            name = raw_name.decode("ascii", errors="strict").strip()
            if (
                name.encode("ascii") != raw_name
                or _HTTP_HEADER_NAME.fullmatch(name) is None
            ):
                raise ValueError("invalid_proxy_header")
            value = raw_value.decode("latin-1").strip()
            if name.casefold() == "connection":
                connection_tokens.update(
                    token.strip().casefold()
                    for token in value.split(",")
                    if token.strip()
                )
            parsed_headers.append((name, value))

        def values(header_name: str) -> list[str]:
            return [
                value
                for name, value in parsed_headers
                if name.casefold() == header_name
            ]

        content_lengths = values("content-length")
        transfer_encodings = values("transfer-encoding")
        if len(content_lengths) > 1 or transfer_encodings:
            raise ValueError("ambiguous_http_framing")
        content_length = (
            content_lengths[0] if content_lengths else None
        )
        if is_control and method in {"POST", "DELETE"}:
            if not parsed.path.startswith(_CONTROL_API_PREFIX):
                raise ValueError("control_path_forbidden")
            content_types = values("content-type")
            if len(content_types) != 1:
                raise ValueError("control_json_required")
            content_type = (
                content_types[0]
                .split(";", 1)[0]
                .strip()
                .casefold()
            )
            if content_type != "application/json":
                raise ValueError("control_json_required")
            if content_length is None or not content_length.isdecimal():
                raise ValueError("control_content_length_required")
            if int(content_length) > _MAX_CONTROL_BODY_BYTES:
                raise ValueError("control_body_too_large")
            if any(
                name.casefold() in _HOP_BY_HOP_HEADERS
                for name, _value in parsed_headers
            ):
                raise ValueError("control_hop_header_forbidden")
        elif content_length is not None:
            raise ValueError("unexpected_http_body")

        authority = (
            f"[{parsed.hostname}]"
            if ":" in parsed.hostname
            else parsed.hostname
        )
        if parsed.port is not None:
            authority = f"{authority}:{parsed.port}"
        host_values = [
            value
            for name, value in parsed_headers
            if name.casefold() == "host"
        ]
        if len(host_values) != 1 or host_values[0].casefold() != authority.casefold():
            raise ValueError("http_host_mismatch")

        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        output = [f"{method} {path} {version}\r\n".encode("ascii")]
        for name, value in parsed_headers:
            normalized = name.casefold()
            if (
                normalized in _HOP_BY_HOP_HEADERS
                or normalized in connection_tokens
                or normalized == "expect"
                or normalized == "content-length"
            ):
                continue
            output.append(f"{name}: {value}\r\n".encode("latin-1"))
        if is_control and method in {"POST", "DELETE"}:
            output.append(
                f"Content-Length: {int(content_length)}\r\n".encode("ascii")
            )
        output.append(b"Connection: close\r\n\r\n")
        return b"".join(output), parsed.hostname, port

    @staticmethod
    def _forwarded_body_length(request: bytes) -> int:
        for line in request.split(b"\r\n")[1:]:
            if line.lower().startswith(b"content-length:"):
                return int(line.split(b":", 1)[1].strip())
        return 0

    async def _copy_limited_http_response(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        total = 0
        while True:
            data = await asyncio.wait_for(
                reader.read(min(64 * 1024, _MAX_HTTP_RESPONSE_BYTES - total + 1)),
                timeout=self._connect_timeout_seconds,
            )
            if not data:
                return
            total += len(data)
            if total > _MAX_HTTP_RESPONSE_BYTES:
                raise ValueError("http_response_too_large")
            writer.write(data)
            await writer.drain()

    @staticmethod
    async def _pipe(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            while data := await reader.read(64 * 1024):
                writer.write(data)
                await writer.drain()
        except (ConnectionError, OSError):
            pass
