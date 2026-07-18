from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import (
    parse_qsl,
    urlencode,
    urljoin,
    urlsplit,
    urlunsplit,
)
from urllib.robotparser import RobotFileParser

import httpx

if TYPE_CHECKING:
    from starter_agent.settings import JobDescriptionToolConfig


IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str], Awaitable[list[ipaddress._BaseAddress]]]
RobotsChecker = Callable[[str, str], Awaitable[bool]]

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "key",
    "signature",
    "token",
    "x-amz-signature",
}
_METADATA_HOSTS = {
    "instance-data",
    "metadata",
    "metadata.aws.internal",
    "metadata.azure.internal",
    "metadata.google.internal",
}
_LOCAL_SUFFIXES = (
    ".home",
    ".internal",
    ".lan",
    ".local",
    ".localdomain",
    ".localhost",
)
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_NUMERIC_LABEL = re.compile(r"^(?:0[xX][0-9a-fA-F]+|[0-9]+)$")
_MAX_URL_LENGTH = 8_192


@dataclass(frozen=True)
class FetchedPage:
    source_url: str
    final_url: str
    status_code: int
    content_type: str
    text: str
    content_sha256: str


class FetchFailure(Exception):
    def __init__(
        self,
        code: str,
        display: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(display)
        self.code = code
        self.display = display
        self.retryable = retryable


@dataclass(frozen=True)
class _ValidatedUrl:
    url: str
    scheme: str
    hostname: str
    port: int
    addresses: tuple[IPAddress, ...]
    selected_address: IPAddress

    @property
    def host_header(self) -> str:
        if isinstance(self.selected_address, ipaddress.IPv6Address):
            # The public hostname can itself be an IPv6 literal.
            try:
                ipaddress.IPv6Address(self.hostname)
            except ValueError:
                return self.hostname
            return f"[{self.hostname}]"
        return self.hostname

    @property
    def pinned_url(self) -> str:
        address = str(self.selected_address)
        if isinstance(self.selected_address, ipaddress.IPv6Address):
            address = f"[{address}]"
        parsed = urlsplit(self.url)
        return urlunsplit(
            (self.scheme, address, parsed.path, parsed.query, "")
        )


def sanitize_public_url(url: str) -> str:
    """Remove fragments and query values that commonly carry credentials."""

    try:
        parsed = urlsplit(url)
        safe_query = urlencode(
            [
                (key, value)
                for key, value in parse_qsl(
                    parsed.query,
                    keep_blank_values=True,
                )
                if key.casefold() not in _SENSITIVE_QUERY_KEYS
            ],
            doseq=True,
        )
        return urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                safe_query,
                "",
            )
        )
    except (TypeError, ValueError):
        # This helper is also used while constructing safe error metadata.
        # Returning no URL is safer than reflecting malformed input.
        return ""


async def default_resolver(host: str) -> list[ipaddress._BaseAddress]:
    """Resolve every stream-capable address without blocking the event loop."""

    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(
        host,
        0,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM,
    )
    addresses: list[IPAddress] = []
    seen: set[IPAddress] = set()
    for _family, _type, _proto, _canonname, sockaddr in records:
        address = ipaddress.ip_address(sockaddr[0])
        if address not in seen:
            seen.add(address)
            addresses.append(address)
    return addresses


class SafeWebFetcher:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        resolver: Resolver = default_resolver,
        robots_checker: RobotsChecker | None = None,
        timeout: float = 10,
        max_response_bytes: int = 1_000_000,
        max_redirects: int = 3,
        user_agent: str = "StarterAgentJobDescription/0.1",
        respect_robots: bool = True,
        _owns_client: bool = False,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        if max_redirects < 0:
            raise ValueError("max_redirects must not be negative")
        if not user_agent or any(
            not 32 <= ord(character) <= 126 for character in user_agent
        ):
            raise ValueError("user_agent must contain printable ASCII")

        self.client = client
        self.resolver = resolver
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.max_redirects = max_redirects
        self.user_agent = user_agent
        self.respect_robots = respect_robots
        self.robots_checker = robots_checker or self._default_robots_checker
        self._owns_client = _owns_client

    @classmethod
    def from_config(
        cls,
        config: JobDescriptionToolConfig,
    ) -> SafeWebFetcher:
        # Environment proxy variables would bypass direct address pinning and
        # introduce an additional trust boundary, so production fetches ignore
        # them. Redirects remain manual and are validated one hop at a time.
        client = httpx.AsyncClient(
            follow_redirects=False,
            trust_env=False,
        )
        return cls(
            client=client,
            resolver=default_resolver,
            timeout=config.fetch_timeout_seconds,
            max_response_bytes=config.max_response_bytes,
            max_redirects=config.max_redirects,
            user_agent=config.user_agent,
            respect_robots=config.respect_robots,
            _owns_client=True,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def fetch(self, url: str) -> FetchedPage:
        current = await self._validate_url(url)
        source_url = sanitize_public_url(current.url)

        for redirect_count in range(self.max_redirects + 1):
            await self._enforce_robots(current)
            try:
                async with self._stream(current) as response:
                    self._validate_connected_peer(response, current)

                    if response.status_code in _REDIRECT_STATUSES:
                        if redirect_count == self.max_redirects:
                            raise FetchFailure(
                                "fetch_failed",
                                "页面重定向次数过多",
                            )
                        location = response.headers.get("location")
                        if not location:
                            raise FetchFailure(
                                "fetch_failed",
                                "页面重定向缺少目标地址",
                            )
                        target = urljoin(current.url, location)
                        current = await self._validate_url(target)
                        continue

                    self._raise_for_status(response.status_code)
                    content_type = self._content_type(response)
                    self._enforce_content_length(response)
                    body = await self._read_limited(response)
                    text = self._decode_body(body, response.encoding)
                    return FetchedPage(
                        source_url=source_url,
                        final_url=sanitize_public_url(current.url),
                        status_code=response.status_code,
                        content_type=content_type,
                        text=text,
                        content_sha256=hashlib.sha256(body).hexdigest(),
                    )
            except FetchFailure:
                raise
            except httpx.TimeoutException as exc:
                raise FetchFailure(
                    "fetch_timeout",
                    "读取岗位页面超时",
                    retryable=True,
                ) from exc
            except httpx.TransportError as exc:
                raise FetchFailure(
                    "fetch_failed",
                    "暂时无法读取岗位页面",
                    retryable=True,
                ) from exc

        raise FetchFailure(
            "fetch_failed",
            "读取岗位页面失败",
            retryable=True,
        )

    async def _validate_url(self, url: str) -> _ValidatedUrl:
        if not isinstance(url, str):
            raise self._unsafe("URL 必须是字符串")
        if (
            not url
            or len(url) > _MAX_URL_LENGTH
            or url != url.strip()
            or any(ord(character) < 32 for character in url)
        ):
            raise self._unsafe("URL 格式不安全")
        # Reject even an empty fragment, rather than silently changing the
        # requested URL's identity.
        if "#" in url:
            raise self._unsafe("URL 不允许包含片段")

        try:
            parsed = urlsplit(url)
            scheme = parsed.scheme.casefold()
            if scheme not in {"http", "https"}:
                raise self._unsafe("只允许 HTTP 或 HTTPS URL")
            if parsed.username is not None or parsed.password is not None:
                raise self._unsafe("URL 不允许包含用户凭据")
            if not parsed.netloc or not parsed.hostname:
                raise self._unsafe("URL 缺少主机名")
            if "\\" in parsed.netloc or "%" in parsed.hostname:
                raise self._unsafe("URL 主机名格式不安全")

            port = parsed.port
        except FetchFailure:
            raise
        except (TypeError, ValueError, UnicodeError) as exc:
            raise self._unsafe("URL 格式无效") from exc

        expected_port = 80 if scheme == "http" else 443
        if port is not None and port != expected_port:
            raise self._unsafe("URL 使用了非标准端口")
        port = expected_port

        hostname = self._canonical_hostname(parsed.hostname)
        literal_address = self._literal_address(hostname)
        if literal_address is not None:
            addresses = (literal_address,)
        else:
            self._validate_dns_name(hostname)
            try:
                resolved = await self.resolver(hostname)
            except FetchFailure:
                raise
            except (OSError, UnicodeError, ValueError) as exc:
                raise FetchFailure(
                    "fetch_failed",
                    "无法解析岗位页面地址",
                    retryable=True,
                ) from exc
            addresses = self._coerce_addresses(resolved)

        if not addresses or any(
            not self._is_public_address(address) for address in addresses
        ):
            raise self._unsafe("URL 指向非公网地址")

        path = parsed.path or "/"
        canonical_url = urlunsplit(
            (
                scheme,
                self._url_netloc(hostname),
                path,
                parsed.query,
                "",
            )
        )
        return _ValidatedUrl(
            url=canonical_url,
            scheme=scheme,
            hostname=hostname,
            port=port,
            addresses=addresses,
            selected_address=addresses[0],
        )

    @staticmethod
    def _canonical_hostname(hostname: str) -> str:
        candidate = hostname.rstrip(".")
        if not candidate:
            raise SafeWebFetcher._unsafe("URL 主机名无效")
        try:
            canonical = candidate.encode("idna").decode("ascii").casefold()
        except (UnicodeError, ValueError) as exc:
            raise SafeWebFetcher._unsafe("URL 主机名无效") from exc
        if len(canonical) > 253:
            raise SafeWebFetcher._unsafe("URL 主机名过长")
        return canonical

    @staticmethod
    def _literal_address(hostname: str) -> IPAddress | None:
        try:
            return ipaddress.ip_address(hostname)
        except ValueError:
            labels = hostname.split(".")
            if labels and all(_NUMERIC_LABEL.fullmatch(label) for label in labels):
                # Browsers and resolver libraries disagree on decimal, octal,
                # shortened, and hexadecimal IPv4 spellings. Reject them
                # instead of allowing a second parser to reinterpret them.
                raise SafeWebFetcher._unsafe("URL 使用了混淆的 IP 地址")
            if ":" in hostname:
                raise SafeWebFetcher._unsafe("URL 使用了无效 IPv6 地址")
            return None

    @staticmethod
    def _validate_dns_name(hostname: str) -> None:
        if (
            hostname == "localhost"
            or hostname in _METADATA_HOSTS
            or any(hostname.endswith(suffix) for suffix in _LOCAL_SUFFIXES)
            or "." not in hostname
        ):
            raise SafeWebFetcher._unsafe("URL 使用了本地或元数据主机名")
        labels = hostname.split(".")
        if any(not _DNS_LABEL.fullmatch(label) for label in labels):
            raise SafeWebFetcher._unsafe("URL 主机名无效")

    @staticmethod
    def _coerce_addresses(
        values: Sequence[ipaddress._BaseAddress],
    ) -> tuple[IPAddress, ...]:
        addresses: list[IPAddress] = []
        seen: set[IPAddress] = set()
        try:
            for value in values:
                address = ipaddress.ip_address(value)
                if address not in seen:
                    seen.add(address)
                    addresses.append(address)
        except (TypeError, ValueError) as exc:
            raise SafeWebFetcher._unsafe("DNS 返回了无效地址") from exc
        return tuple(addresses)

    @staticmethod
    def _is_public_address(address: IPAddress) -> bool:
        return (
            address.is_global
            and not address.is_private
            and not address.is_loopback
            and not address.is_link_local
            and not address.is_multicast
            and not address.is_reserved
            and not address.is_unspecified
        )

    @staticmethod
    def _url_netloc(hostname: str) -> str:
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            return hostname
        if isinstance(address, ipaddress.IPv6Address):
            return f"[{hostname}]"
        return hostname

    @staticmethod
    def _unsafe(display: str) -> FetchFailure:
        return FetchFailure("unsafe_url", display)

    def _stream(
        self,
        target: _ValidatedUrl,
        *,
        accept: str = "text/html,text/plain;q=0.9",
    ):
        headers = {
            "User-Agent": self.user_agent,
            "Accept": accept,
            "Host": target.host_header,
            # Avoid pooling one pinned IP connection across different host
            # names, where the original TLS SNI could otherwise be reused.
            "Connection": "close",
        }
        extensions: dict[str, object] = {}
        if target.scheme == "https":
            extensions["sni_hostname"] = target.hostname
        return self.client.stream(
            "GET",
            target.pinned_url,
            headers=headers,
            follow_redirects=False,
            timeout=self.timeout,
            extensions=extensions,
        )

    async def _enforce_robots(self, target: _ValidatedUrl) -> None:
        if not self.respect_robots:
            return
        try:
            allowed = await self.robots_checker(target.url, self.user_agent)
        except FetchFailure:
            raise
        except (httpx.HTTPError, OSError, ValueError) as exc:
            raise FetchFailure(
                "robots_blocked",
                "无法安全确认目标网站的 robots 规则",
            ) from exc
        if not allowed:
            raise FetchFailure(
                "robots_blocked",
                "目标网站不允许自动读取该页面",
            )

    async def _default_robots_checker(
        self,
        target_url: str,
        user_agent: str,
    ) -> bool:
        parsed = urlsplit(target_url)
        robots_url = urlunsplit(
            (parsed.scheme, parsed.netloc, "/robots.txt", "", "")
        )
        try:
            target = await self._validate_url(robots_url)
            async with self._stream(
                target,
                accept="text/plain,text/html;q=0.5",
            ) as response:
                self._validate_connected_peer(response, target)
                status = response.status_code
                if status in {404, 410} or 400 <= status < 500:
                    # RFC 9309 treats 4xx as "unavailable"; access is allowed.
                    # Authentication and rate limiting are conservative
                    # exceptions because they are explicit access controls.
                    return status not in {401, 403, 429}
                if status != 200:
                    return False
                media_type = response.headers.get(
                    "content-type", ""
                ).split(";", 1)[0].strip().casefold()
                if media_type not in {"text/plain", "text/html"}:
                    return False
                self._enforce_content_length(response)
                body = await self._read_limited(response)
                policy = self._decode_body(body, response.encoding)
        except (FetchFailure, httpx.HTTPError, OSError, ValueError):
            # When policy cannot be retrieved safely, fail closed. This tool
            # does not attempt to bypass a site's automation preference.
            return False

        parser = RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(policy.splitlines())
        return parser.can_fetch(user_agent, target_url)

    @staticmethod
    def _validate_connected_peer(
        response: httpx.Response,
        target: _ValidatedUrl,
    ) -> None:
        stream = response.extensions.get("network_stream")
        if stream is None or not hasattr(stream, "get_extra_info"):
            # Mock transports do not expose a peer. Production uses a URL
            # pinned to selected_address, so DNS cannot be re-resolved here.
            return
        try:
            peer = stream.get_extra_info("server_addr")
            if not peer:
                return
            peer_address = ipaddress.ip_address(peer[0])
        except (AttributeError, IndexError, TypeError, ValueError) as exc:
            raise FetchFailure(
                "unsafe_url",
                "无法验证岗位页面的网络地址",
            ) from exc
        if (
            peer_address != target.selected_address
            or not SafeWebFetcher._is_public_address(peer_address)
        ):
            raise FetchFailure(
                "unsafe_url",
                "岗位页面连接到了未经验证的网络地址",
            )

    @staticmethod
    def _content_type(response: httpx.Response) -> str:
        content_type = response.headers.get(
            "content-type", ""
        ).split(";", 1)[0].strip().casefold()
        if content_type not in {"text/html", "text/plain"}:
            raise FetchFailure(
                "unsupported_content_type",
                "目标页面不是 HTML 或纯文本",
            )
        return content_type

    def _enforce_content_length(self, response: httpx.Response) -> None:
        raw_length = response.headers.get("content-length")
        if raw_length is None:
            return
        try:
            declared_length = int(raw_length)
        except ValueError:
            return
        if declared_length > self.max_response_bytes:
            raise FetchFailure(
                "response_too_large",
                "目标页面超过读取大小限制",
            )

    async def _read_limited(self, response: httpx.Response) -> bytes:
        body = bytearray()
        async for chunk in response.aiter_bytes():
            if len(body) + len(chunk) > self.max_response_bytes:
                raise FetchFailure(
                    "response_too_large",
                    "目标页面超过读取大小限制",
                )
            body.extend(chunk)
        return bytes(body)

    @staticmethod
    def _decode_body(body: bytes, encoding: str | None) -> str:
        try:
            return body.decode(encoding or "utf-8", errors="replace")
        except LookupError:
            return body.decode("utf-8", errors="replace")

    @staticmethod
    def _raise_for_status(status_code: int) -> None:
        if 200 <= status_code < 300:
            return
        if status_code == 401:
            raise FetchFailure(
                "authentication_required",
                "目标页面要求登录",
            )
        if status_code == 403:
            raise FetchFailure(
                "access_blocked",
                "目标页面拒绝自动访问",
            )
        if status_code in {404, 410}:
            raise FetchFailure(
                "job_not_found",
                "岗位页面不存在或已下线",
            )
        if status_code == 429:
            raise FetchFailure(
                "rate_limited",
                "目标网站暂时限制访问频率",
                retryable=True,
            )
        raise FetchFailure(
            "fetch_failed",
            f"岗位页面返回 HTTP {status_code}",
            retryable=status_code >= 500,
        )
