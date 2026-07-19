from __future__ import annotations

import asyncio
import codecs
import hashlib
import ipaddress
import re
import socket
import unicodedata
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import (
    parse_qsl,
    urlencode,
    urljoin,
    urlsplit,
    urlunsplit,
    unquote_plus,
)
from urllib.robotparser import RobotFileParser

import httpx

if TYPE_CHECKING:
    from starter_agent.settings import JobDescriptionToolConfig


IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str], Awaitable[list[IPAddress]]]
RobotsChecker = Callable[[str, str], Awaitable[bool]]

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_SENSITIVE_QUERY_KEY_PARTS = (
    "credential",
    "password",
    "passwd",
    "secret",
    "signature",
    "token",
)
_SENSITIVE_QUERY_KEY_FAMILIES = (
    "accesskey",
    "apikey",
    "subscriptionkey",
)
_SENSITIVE_QUERY_KEYS = frozenset(
    {
        "apikey",
        "auth",
        "authentication",
        "authorization",
        "code",
        "googleaccessid",
        "key",
        "pwd",
        "se",
        "sig",
        "sip",
        "skoid",
        "sks",
        "skt",
        "sktid",
        "skv",
        "sp",
        "spr",
        "sr",
        "srt",
        "ss",
        "st",
        "sv",
        "xamzalgorithm",
        "xamzdate",
        "xamzexpires",
        "xamzsignedheaders",
        "xgoogalgorithm",
        "xgoogdate",
        "xgoogexpires",
        "xgoogsignedheaders",
    }
)
_SAFE_QUERY_FIELD_LIMIT = 256
_CHARSET_PARAMETER = re.compile(
    r"(?:^|;)\s*charset\s*=\s*[\"']?\s*([a-zA-Z0-9._:+-]+)",
    re.IGNORECASE,
)
_HTML_META_CHARSET = re.compile(
    rb"<meta\b[^>]{0,1024}\bcharset\s*=\s*[\"']?\s*"
    rb"([a-zA-Z0-9._:+-]+)",
    re.IGNORECASE,
)
_CHARSET_SNIFF_BYTES = 8_192
_BOM_ENCODINGS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_BE, "utf-16"),
    (codecs.BOM_UTF16_LE, "utf-16"),
)
_SUPPORTED_IDENTITY_ENCODINGS = {"", "identity"}
_CLOUD_METADATA_HOSTS = {
    "instance-data",
    "instance-data.ec2.internal",
    "metadata",
    "metadata.aws.internal",
    "metadata.azure.internal",
    "metadata.google.internal",
}
_CLOUD_PLATFORM_ENDPOINT_ADDRESSES = frozenset(
    {
        # AWS, GCP, Azure, and Oracle instance metadata (IPv4).
        ipaddress.ip_address("169.254.169.254"),
        # Azure platform virtual IP / WireServer.
        ipaddress.ip_address("168.63.129.16"),
        # Alibaba Cloud instance metadata.
        ipaddress.ip_address("100.100.100.200"),
        # AWS IMDS IPv6 endpoint.
        ipaddress.ip_address("fd00:ec2::254"),
    }
)
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
        if not isinstance(url, str) or _has_forbidden_url_codepoint(url):
            return ""
        parsed = urlsplit(url)
        raw_fields = re.split(r"[&;]", parsed.query)
        if len(raw_fields) > _SAFE_QUERY_FIELD_LIMIT:
            raw_fields = []
        safe_pairs: list[tuple[str, str]] = []
        for field in raw_fields:
            if not field:
                continue
            pairs = parse_qsl(
                field,
                keep_blank_values=True,
                max_num_fields=2,
            )
            for key, value in pairs:
                if not _is_sensitive_query_key(key):
                    safe_pairs.append((key, value))
        safe_query = urlencode(
            safe_pairs,
            doseq=True,
        )
        hostname = parsed.hostname
        if not hostname:
            return ""
        canonical_host = hostname.encode("idna").decode("ascii").casefold()
        try:
            address = ipaddress.ip_address(canonical_host)
        except ValueError:
            netloc = canonical_host
        else:
            netloc = (
                f"[{canonical_host}]"
                if isinstance(address, ipaddress.IPv6Address)
                else canonical_host
            )
        if parsed.port is not None:
            netloc = f"{netloc}:{parsed.port}"
        return urlunsplit(
            (
                parsed.scheme,
                netloc,
                parsed.path,
                safe_query,
                "",
            )
        )
    except (TypeError, ValueError):
        # This helper is also used while constructing safe error metadata.
        # Returning no URL is safer than reflecting malformed input.
        return ""


def _is_sensitive_query_key(key: str) -> bool:
    decoded = key
    for _ in range(2):
        decoded = unquote_plus(decoded)
    normalized = re.sub(r"[^a-z0-9]", "", decoded.casefold())
    return (
        normalized in _SENSITIVE_QUERY_KEYS
        or normalized.startswith(("auth", "oauth"))
        or any(part in normalized for part in _SENSITIVE_QUERY_KEY_PARTS)
        or any(
            family in normalized
            for family in _SENSITIVE_QUERY_KEY_FAMILIES
        )
    )


def _has_forbidden_url_codepoint(value: str) -> bool:
    return any(
        unicodedata.category(character) in {"Cc", "Cs"}
        for character in value
    )


async def default_resolver(host: str) -> list[IPAddress]:
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
        require_peer_metadata: bool = False,
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
        self.robots_checker = robots_checker
        self.require_peer_metadata = (
            require_peer_metadata or _owns_client
        )
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
            require_peer_metadata=True,
            _owns_client=True,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def fetch(self, url: str) -> FetchedPage:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout
        try:
            async with asyncio.timeout(self.timeout):
                return await self._fetch_with_deadline(url, deadline)
        except FetchFailure:
            raise
        except TimeoutError as exc:
            raise FetchFailure(
                "fetch_timeout",
                "读取岗位页面超时",
                retryable=True,
            ) from exc
        except httpx.TimeoutException as exc:
            raise FetchFailure(
                "fetch_timeout",
                "读取岗位页面超时",
                retryable=True,
            ) from exc
        except (httpx.InvalidURL, UnicodeError) as exc:
            raise self._unsafe("URL 格式无法安全编码") from exc
        except httpx.DecodingError as exc:
            raise FetchFailure(
                "fetch_failed",
                "岗位页面响应无法安全解码",
                retryable=True,
            ) from exc
        except httpx.TransportError as exc:
            raise FetchFailure(
                "fetch_failed",
                "暂时无法读取岗位页面",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise FetchFailure(
                "fetch_failed",
                "岗位页面响应流处理失败",
                retryable=True,
            ) from exc

    async def _fetch_with_deadline(
        self,
        url: str,
        deadline: float,
    ) -> FetchedPage:
        current = await self._validate_url(url)
        source_url = sanitize_public_url(current.url)

        for redirect_count in range(self.max_redirects + 1):
            await self._enforce_robots(current, deadline)
            async with self._stream(
                current,
                timeout=self._remaining_timeout(deadline),
            ) as response:
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
                    current = await self._validate_url(
                        urljoin(current.url, location)
                    )
                    continue

                self._raise_for_status(response.status_code)
                self._enforce_content_encoding(response)
                content_type = self._content_type(response)
                self._enforce_content_length(response)
                body = await self._read_limited(response)
                text = self._decode_body(
                    body,
                    response.headers.get("content-type", ""),
                    content_type,
                )
                return FetchedPage(
                    source_url=source_url,
                    final_url=sanitize_public_url(current.url),
                    status_code=response.status_code,
                    content_type=content_type,
                    text=text,
                    content_sha256=hashlib.sha256(body).hexdigest(),
                )

        raise FetchFailure(
            "fetch_failed",
            "读取岗位页面失败",
            retryable=True,
        )

    @staticmethod
    def _remaining_timeout(deadline: float) -> float:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError
        return remaining

    async def _validate_url(self, url: str) -> _ValidatedUrl:
        if not isinstance(url, str):
            raise self._unsafe("URL 必须是字符串")
        if (
            not url
            or len(url) > _MAX_URL_LENGTH
            or url != url.strip()
            or _has_forbidden_url_codepoint(url)
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
            except TimeoutError:
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
            or hostname in _CLOUD_METADATA_HOSTS
            or any(hostname.endswith(suffix) for suffix in _LOCAL_SUFFIXES)
            or "." not in hostname
        ):
            raise SafeWebFetcher._unsafe("URL 使用了本地或元数据主机名")
        labels = hostname.split(".")
        if any(not _DNS_LABEL.fullmatch(label) for label in labels):
            raise SafeWebFetcher._unsafe("URL 主机名无效")

    @staticmethod
    def _coerce_addresses(
        values: Sequence[IPAddress],
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
            address not in _CLOUD_PLATFORM_ENDPOINT_ADDRESSES
            and address.is_global
            and not address.is_private
            and not address.is_loopback
            and not address.is_link_local
            and not address.is_multicast
            and not address.is_reserved
            and not address.is_unspecified
            and not (
                isinstance(address, ipaddress.IPv6Address)
                and address.is_site_local
            )
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
        timeout: float,
    ):
        headers = {
            "User-Agent": self.user_agent,
            "Accept": accept,
            "Accept-Encoding": "identity",
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
            timeout=timeout,
            extensions=extensions,
        )

    async def _enforce_robots(
        self,
        target: _ValidatedUrl,
        deadline: float,
    ) -> None:
        if not self.respect_robots:
            return
        try:
            if self.robots_checker is None:
                allowed = await self._default_robots_checker(
                    target.url,
                    self.user_agent,
                    deadline,
                )
            else:
                allowed = await self.robots_checker(
                    target.url,
                    self.user_agent,
                )
        except FetchFailure:
            raise
        except (TimeoutError, httpx.TimeoutException):
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
        deadline: float,
    ) -> bool:
        parsed = urlsplit(target_url)
        robots_url = urlunsplit(
            (parsed.scheme, parsed.netloc, "/robots.txt", "", "")
        )
        try:
            target = await self._validate_url(robots_url)
            for redirect_count in range(self.max_redirects + 1):
                async with self._stream(
                    target,
                    accept="text/plain,text/html;q=0.5",
                    timeout=self._remaining_timeout(deadline),
                ) as response:
                    self._validate_connected_peer(response, target)
                    status = response.status_code
                    if status in _REDIRECT_STATUSES:
                        if redirect_count == self.max_redirects:
                            return False
                        location = response.headers.get("location")
                        if not location:
                            return False
                        target = await self._validate_url(
                            urljoin(target.url, location)
                        )
                        continue
                    if status in {404, 410} or 400 <= status < 500:
                        return status not in {401, 403, 429}
                    if status != 200:
                        return False
                    self._enforce_content_encoding(response)
                    media_type = response.headers.get(
                        "content-type", ""
                    ).split(";", 1)[0].strip().casefold()
                    if media_type not in {"text/plain", "text/html"}:
                        return False
                    self._enforce_content_length(response)
                    body = await self._read_limited(response)
                    policy = self._decode_body(
                        body,
                        response.headers.get("content-type", ""),
                        media_type,
                    )
                    robots_url = target.url
                    break
            else:
                return False
        except (TimeoutError, httpx.TimeoutException):
            raise
        except (
            FetchFailure,
            httpx.HTTPError,
            OSError,
            UnicodeError,
            ValueError,
        ):
            # When policy cannot be retrieved safely, fail closed. This tool
            # does not attempt to bypass a site's automation preference.
            return False

        parser = RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(policy.splitlines())
        return parser.can_fetch(user_agent, target_url)

    def _validate_connected_peer(
        self,
        response: httpx.Response,
        target: _ValidatedUrl,
    ) -> None:
        stream = response.extensions.get("network_stream")
        if stream is None or not hasattr(stream, "get_extra_info"):
            if self.require_peer_metadata:
                raise FetchFailure(
                    "unsafe_url",
                    "网络传输未提供可验证的对端地址",
                )
            return
        try:
            peer = stream.get_extra_info("server_addr")
            if not peer:
                if self.require_peer_metadata:
                    raise FetchFailure(
                        "unsafe_url",
                        "网络传输未提供可验证的对端地址",
                    )
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

    @staticmethod
    def _enforce_content_encoding(response: httpx.Response) -> None:
        raw_encoding = response.headers.get("content-encoding", "")
        encodings = {
            item.strip().casefold()
            for item in raw_encoding.split(",")
        }
        if not encodings.issubset(_SUPPORTED_IDENTITY_ENCODINGS):
            raise FetchFailure(
                "fetch_failed",
                "目标页面使用了不支持的内容压缩",
            )

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
        async for chunk in response.aiter_raw():
            if len(body) + len(chunk) > self.max_response_bytes:
                raise FetchFailure(
                    "response_too_large",
                    "目标页面超过读取大小限制",
                )
            body.extend(chunk)
        return bytes(body)

    @staticmethod
    def _decode_body(
        body: bytes,
        content_type_header: str,
        media_type: str,
    ) -> str:
        encoding = SafeWebFetcher._valid_http_charset(content_type_header)
        if encoding is None:
            for marker, candidate in _BOM_ENCODINGS:
                if body.startswith(marker):
                    encoding = candidate
                    break
        if encoding is None and media_type == "text/html":
            match = _HTML_META_CHARSET.search(
                body[:_CHARSET_SNIFF_BYTES]
            )
            if match is not None:
                candidate = match.group(1).decode("ascii")
                if SafeWebFetcher._is_text_codec(candidate):
                    encoding = candidate
        try:
            return body.decode(encoding or "utf-8", errors="replace")
        except (LookupError, TypeError, ValueError):
            return body.decode("utf-8", errors="replace")

    @staticmethod
    def _valid_http_charset(content_type_header: str) -> str | None:
        match = _CHARSET_PARAMETER.search(content_type_header)
        if match is None:
            return None
        candidate = match.group(1)
        return (
            candidate
            if SafeWebFetcher._is_text_codec(candidate)
            else None
        )

    @staticmethod
    def _is_text_codec(name: str) -> bool:
        try:
            codecs.lookup(name)
            b"".decode(name)
        except (LookupError, TypeError, ValueError):
            return False
        return True

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
