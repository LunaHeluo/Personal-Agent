from __future__ import annotations

import asyncio
import gzip
import ipaddress
from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from starter_agent.settings import JobDescriptionToolConfig
from starter_agent.tools.adapters import safe_web_fetcher as fetcher_module
from starter_agent.tools.adapters.safe_web_fetcher import (
    FetchFailure,
    IPAddress,
    SafeWebFetcher,
    sanitize_public_url,
)


PUBLIC_IPV4 = ipaddress.ip_address("93.184.216.34")
AIJOBS_JOB_URL = (
    "https://aijobs.ai/job/strategic-sales-manager-ai-llm-1"
)


class StaticStream(httpx.AsyncByteStream):
    def __init__(self, content: bytes) -> None:
        self.content = content

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self.content

    async def aclose(self) -> None:
        return None


async def public_resolver(host: str) -> list[IPAddress]:
    return [PUBLIC_IPV4]


async def allow_robots(url: str, user_agent: str) -> bool:
    return True


def make_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def html_response(text: str = "<h1>AI PM</h1>") -> httpx.Response:
    return httpx.Response(
        200,
        headers={"content-type": "text/html; charset=utf-8"},
        stream=StaticStream(text.encode()),
    )


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/job",
        "http://localhost/job",
        "http://localhost./job",
        "http://service.local/job",
        "http://intranet/job",
        "http://127.0.0.1/job",
        "http://127.1/job",
        "http://0177.0.0.1/job",
        "http://0x7f000001/job",
        "http://2130706433/job",
        "http://[::1]/job",
        "http://[fe80::1]/job",
        "https://[fec0::1]/job",
        "http://169.254.169.254/latest/meta-data",
        "http://168.63.129.16/machine/?comp=goalstate",
        "http://100.100.100.200/latest/meta-data",
        "http://[fd00:ec2::254]/latest/meta-data",
        "https://metadata.google.internal/computeMetadata/v1/",
        "https://user:pass@example.com/job",
        "https://example.com:8443/job",
        "https://example.com/job#details",
        "https://-bad.example/job",
        "https://example..com/job",
        " https://example.com/job",
        "https://example.com\\@127.0.0.1/job",
        "https://example.com/\x7f",
        "https://example.com/\x85",
        "https://example.com/\ud800",
        "https://example.com/?q=\ud800",
    ],
)
async def test_rejects_unsafe_urls_without_requesting(url: str) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return html_response()

    async with make_client(handler) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=public_resolver,
            robots_checker=allow_robots,
        )

        with pytest.raises(FetchFailure) as exc:
            await fetcher.fetch(url)

    assert exc.value.code == "unsafe_url"
    assert requests == []


async def test_accepts_idna_host_and_pins_the_validated_address() -> None:
    resolved_hosts: list[str] = []
    requests: list[httpx.Request] = []

    async def resolver(host: str) -> list[IPAddress]:
        resolved_hosts.append(host)
        return [PUBLIC_IPV4]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return html_response()

    async with make_client(handler) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=resolver,
            robots_checker=allow_robots,
        )
        page = await fetcher.fetch("https://bücher.example/job")

    assert resolved_hosts == ["xn--bcher-kva.example"]
    assert requests[0].url.host == str(PUBLIC_IPV4)
    assert requests[0].headers["host"] == "xn--bcher-kva.example"
    assert requests[0].headers["accept-encoding"] == "identity"
    assert (
        requests[0].extensions["sni_hostname"]
        == "xn--bcher-kva.example"
    )
    assert page.final_url == "https://xn--bcher-kva.example/job"


async def test_accepts_and_pins_public_ipv6_literal() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return html_response()

    async with make_client(handler) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=public_resolver,
            robots_checker=allow_robots,
        )
        page = await fetcher.fetch(
            "https://[2606:4700:4700::1111]/job"
        )

    assert requests[0].url.host == "2606:4700:4700::1111"
    assert requests[0].headers["host"] == "[2606:4700:4700::1111]"
    assert page.final_url == "https://[2606:4700:4700::1111]/job"


async def test_rejects_private_or_mixed_dns_answers() -> None:
    async def mixed_resolver(host: str) -> list[IPAddress]:
        return [PUBLIC_IPV4, ipaddress.ip_address("10.0.0.8")]

    async with make_client(lambda request: html_response()) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=mixed_resolver,
            robots_checker=allow_robots,
        )

        with pytest.raises(FetchFailure) as exc:
            await fetcher.fetch("https://example.com/job")

    assert exc.value.code == "unsafe_url"


async def test_rejects_site_local_ipv6_from_dns_answer() -> None:
    requests: list[httpx.Request] = []

    async def site_local_resolver(host: str) -> list[IPAddress]:
        return [ipaddress.ip_address("fec0::1")]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return html_response()

    async with make_client(handler) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=site_local_resolver,
            robots_checker=allow_robots,
        )

        with pytest.raises(FetchFailure) as exc:
            await fetcher.fetch("https://jobs.example/job")

    assert exc.value.code == "unsafe_url"
    assert requests == []


async def test_rejects_azure_platform_ip_from_dns_answer() -> None:
    requests: list[httpx.Request] = []

    async def azure_resolver(host: str) -> list[IPAddress]:
        return [ipaddress.ip_address("168.63.129.16")]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return html_response()

    async with make_client(handler) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=azure_resolver,
            robots_checker=allow_robots,
        )

        with pytest.raises(FetchFailure) as exc:
            await fetcher.fetch("https://jobs.example/job")

    assert exc.value.code == "unsafe_url"
    assert requests == []


async def test_empty_dns_answer_fails_closed() -> None:
    async def empty_resolver(host: str) -> list[IPAddress]:
        return []

    async with make_client(lambda request: html_response()) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=empty_resolver,
            robots_checker=allow_robots,
        )

        with pytest.raises(FetchFailure) as exc:
            await fetcher.fetch("https://example.com/job")

    assert exc.value.code == "unsafe_url"


async def test_redirect_to_private_ip_is_rejected_before_second_request() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"location": "http://10.0.0.8/job"})

    async with make_client(handler) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=public_resolver,
            robots_checker=allow_robots,
        )

        with pytest.raises(FetchFailure) as exc:
            await fetcher.fetch("https://example.com/job")

    assert exc.value.code == "unsafe_url"
    assert len(requests) == 1


async def test_redirect_to_azure_platform_ip_is_rejected() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            302,
            headers={
                "location": (
                    "http://168.63.129.16/machine/?comp=goalstate"
                )
            },
        )

    async with make_client(handler) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=public_resolver,
            robots_checker=allow_robots,
        )

        with pytest.raises(FetchFailure) as exc:
            await fetcher.fetch("https://example.com/job")

    assert exc.value.code == "unsafe_url"
    assert len(requests) == 1


async def test_rejects_peer_that_does_not_match_pinned_address() -> None:
    class WrongPeer:
        def get_extra_info(self, name: str):
            assert name == "server_addr"
            return ("10.0.0.8", 443)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"<h1>AI PM</h1>",
            extensions={"network_stream": WrongPeer()},
        )

    async with make_client(handler) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=public_resolver,
            robots_checker=allow_robots,
        )

        with pytest.raises(FetchFailure) as exc:
            await fetcher.fetch("https://example.com/job")

    assert exc.value.code == "unsafe_url"


async def test_checks_robots_again_after_cross_origin_redirect() -> None:
    checked: list[str] = []
    requests: list[httpx.Request] = []

    async def checker(url: str, user_agent: str) -> bool:
        checked.append(url)
        return "blocked.example" not in url

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            302,
            headers={"location": "https://blocked.example/job"},
        )

    async with make_client(handler) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=public_resolver,
            robots_checker=checker,
        )

        with pytest.raises(FetchFailure) as exc:
            await fetcher.fetch("https://example.com/job")

    assert exc.value.code == "robots_blocked"
    assert checked == [
        "https://example.com/job",
        "https://blocked.example/job",
    ]
    assert len(requests) == 1


async def test_more_than_three_redirects_returns_fetch_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        index = int(request.url.path.removeprefix("/job/"))
        return httpx.Response(302, headers={"location": f"/job/{index + 1}"})

    async with make_client(handler) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=public_resolver,
            robots_checker=allow_robots,
            max_redirects=3,
        )

        with pytest.raises(FetchFailure) as exc:
            await fetcher.fetch("https://example.com/job/0")

    assert exc.value.code == "fetch_failed"
    assert exc.value.retryable is False


async def test_robots_denial_does_not_request_the_job_page() -> None:
    requests: list[httpx.Request] = []

    async def deny_robots(url: str, user_agent: str) -> bool:
        return False

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return html_response()

    async with make_client(handler) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=public_resolver,
            robots_checker=deny_robots,
        )

        with pytest.raises(FetchFailure) as exc:
            await fetcher.fetch("https://example.com/job")

    assert exc.value.code == "robots_blocked"
    assert requests == []


async def test_production_robots_checker_reads_policy_and_blocks_page() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        assert request.headers["host"] == "example.com"
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200,
                headers={"content-type": "text/plain"},
                stream=StaticStream(
                    b"User-agent: *\nDisallow: /job\n"
                ),
            )
        return html_response()

    async with make_client(handler) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=public_resolver,
        )

        with pytest.raises(FetchFailure) as exc:
            await fetcher.fetch("https://example.com/job")

    assert exc.value.code == "robots_blocked"
    assert paths == ["/robots.txt"]


async def test_aijobs_job_url_reports_explicit_robots_policy_block() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        assert request.headers["host"] == "aijobs.ai"
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200,
                headers={"content-type": "text/plain"},
                stream=StaticStream(
                    b"User-agent: *\nDisallow: /job/\n"
                ),
            )
        return html_response()

    async with make_client(handler) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=public_resolver,
        )

        with pytest.raises(FetchFailure) as exc:
            await fetcher.fetch(AIJOBS_JOB_URL)

    assert exc.value.code == "robots_blocked"
    assert "robots.txt" in exc.value.display
    assert paths == ["/robots.txt"]


async def test_production_robots_checker_follows_safe_redirect() -> None:
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        host_and_path = (request.headers["host"], request.url.path)
        requests.append(host_and_path)
        assert request.headers["accept-encoding"] == "identity"
        if host_and_path == ("example.com", "/robots.txt"):
            return httpx.Response(
                302,
                headers={
                    "location": "https://policy.example/robots.txt"
                },
            )
        if host_and_path == ("policy.example", "/robots.txt"):
            return httpx.Response(
                200,
                headers={"content-type": "text/plain"},
                stream=StaticStream(b"User-agent: *\nAllow: /\n"),
            )
        return html_response()

    async with make_client(handler) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=public_resolver,
        )
        page = await fetcher.fetch("https://example.com/job")

    assert page.status_code == 200
    assert requests == [
        ("example.com", "/robots.txt"),
        ("policy.example", "/robots.txt"),
        ("example.com", "/job"),
    ]


async def test_production_robots_checker_rejects_unsafe_redirect() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            302,
            headers={"location": "http://127.0.0.1/robots.txt"},
        )

    async with make_client(handler) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=public_resolver,
        )

        with pytest.raises(FetchFailure) as exc:
            await fetcher.fetch("https://example.com/job")

    assert exc.value.code == "robots_blocked"
    assert len(requests) == 1


async def test_production_robots_redirect_budget_fails_closed() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            302,
            headers={"location": f"/robots-{len(requests)}"},
        )

    async with make_client(handler) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=public_resolver,
            max_redirects=1,
        )

        with pytest.raises(FetchFailure) as exc:
            await fetcher.fetch("https://example.com/job")

    assert exc.value.code == "robots_blocked"
    assert len(requests) == 2


async def test_production_robots_rejects_content_encoding() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(
            200,
            headers={
                "content-type": "text/plain",
                "content-encoding": "gzip",
            },
            content=gzip.compress(b"User-agent: *\nAllow: /\n"),
        )

    async with make_client(handler) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=public_resolver,
        )

        with pytest.raises(FetchFailure) as exc:
            await fetcher.fetch("https://example.com/job")

    assert exc.value.code == "robots_blocked"
    assert paths == ["/robots.txt"]


@pytest.mark.parametrize(
    ("robots_status", "allowed"),
    [
        (404, True),
        (410, True),
        (401, False),
        (403, False),
        (429, False),
        (500, False),
    ],
)
async def test_production_robots_status_policy(
    robots_status: int,
    allowed: bool,
) -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(robots_status)
        return html_response()

    async with make_client(handler) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=public_resolver,
        )
        if allowed:
            page = await fetcher.fetch("https://example.com/job")
            assert page.status_code == 200
            assert paths == ["/robots.txt", "/job"]
        else:
            with pytest.raises(FetchFailure) as exc:
                await fetcher.fetch("https://example.com/job")
            assert exc.value.code == "robots_blocked"
            assert paths == ["/robots.txt"]


async def test_dns_resolution_uses_whole_fetch_deadline() -> None:
    async def hanging_resolver(host: str) -> list[IPAddress]:
        await asyncio.sleep(60)
        return [PUBLIC_IPV4]

    async with make_client(lambda request: html_response()) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=hanging_resolver,
            robots_checker=allow_robots,
            timeout=0.01,
        )

        with pytest.raises(FetchFailure) as exc:
            await fetcher.fetch("https://example.com/job")

    assert exc.value.code == "fetch_timeout"
    assert exc.value.retryable is True


@pytest.mark.parametrize(
    ("status_code", "code", "retryable"),
    [
        (401, "authentication_required", False),
        (403, "access_blocked", False),
        (404, "job_not_found", False),
        (410, "job_not_found", False),
        (429, "rate_limited", True),
        (500, "fetch_failed", True),
    ],
)
async def test_maps_http_status(
    status_code: int,
    code: str,
    retryable: bool,
) -> None:
    async with make_client(
        lambda request: httpx.Response(status_code)
    ) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=public_resolver,
            robots_checker=allow_robots,
        )

        with pytest.raises(FetchFailure) as exc:
            await fetcher.fetch("https://example.com/job")

    assert exc.value.code == code
    assert exc.value.retryable is retryable


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (httpx.ReadTimeout("slow"), "fetch_timeout"),
        (httpx.ConnectError("offline"), "fetch_failed"),
    ],
)
async def test_maps_transport_errors(
    error: httpx.TransportError,
    code: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    async with make_client(handler) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=public_resolver,
            robots_checker=allow_robots,
        )

        with pytest.raises(FetchFailure) as exc:
            await fetcher.fetch("https://example.com/job")

    assert exc.value.code == code
    assert exc.value.retryable is True


async def test_rejects_unsupported_content_type() -> None:
    async with make_client(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=b"%PDF",
        )
    ) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=public_resolver,
            robots_checker=allow_robots,
        )

        with pytest.raises(FetchFailure) as exc:
            await fetcher.fetch("https://example.com/job")

    assert exc.value.code == "unsupported_content_type"


class CountingStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.yielded = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.yielded += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


async def test_stops_stream_when_response_exceeds_budget() -> None:
    stream = CountingStream([b"a" * 600_000, b"b" * 600_000, b"c"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            stream=stream,
        )

    async with make_client(handler) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=public_resolver,
            robots_checker=allow_robots,
            max_response_bytes=1_000_000,
        )

        with pytest.raises(FetchFailure) as exc:
            await fetcher.fetch("https://example.com/job")

    assert exc.value.code == "response_too_large"
    assert stream.yielded == 2
    assert stream.closed is True


async def test_rejects_invalid_compressed_stream_before_decoding() -> None:
    stream = CountingStream([b"this is not a gzip stream"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-type": "text/html",
                "content-encoding": "gzip",
            },
            stream=stream,
        )

    async with make_client(handler) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=public_resolver,
            robots_checker=allow_robots,
        )

        with pytest.raises(FetchFailure) as exc:
            await fetcher.fetch("https://example.com/job")

    assert exc.value.code == "fetch_failed"
    assert exc.value.retryable is False
    assert stream.yielded == 0
    assert stream.closed is True


async def test_rejects_valid_high_expansion_gzip_before_decoding() -> None:
    compressed = gzip.compress(b"a" * 5_000_000)
    assert len(compressed) < 10_000
    stream = CountingStream([compressed])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(
            200,
            headers={
                "content-type": "text/html",
                "content-encoding": "gzip",
            },
            stream=stream,
        )

    async with make_client(handler) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=public_resolver,
            robots_checker=allow_robots,
        )

        with pytest.raises(FetchFailure) as exc:
            await fetcher.fetch("https://example.com/job")

    assert exc.value.code == "fetch_failed"
    assert exc.value.retryable is False
    assert stream.yielded == 0
    assert stream.closed is True


async def test_rejects_declared_oversized_response_before_streaming() -> None:
    stream = CountingStream([b"never read"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-type": "text/plain",
                "content-length": "1000001",
            },
            stream=stream,
        )

    async with make_client(handler) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=public_resolver,
            robots_checker=allow_robots,
            max_response_bytes=1_000_000,
        )

        with pytest.raises(FetchFailure) as exc:
            await fetcher.fetch("https://example.com/job")

    assert exc.value.code == "response_too_large"
    assert stream.yielded == 0
    assert stream.closed is True


async def test_streaming_uses_one_whole_fetch_deadline() -> None:
    class SlowStream(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.closed = False

        async def __aiter__(self) -> AsyncIterator[bytes]:
            await asyncio.sleep(0.02)
            yield b"first"
            await asyncio.sleep(0.02)
            yield b"second"

        async def aclose(self) -> None:
            self.closed = True

    stream = SlowStream()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            stream=stream,
        )

    async with make_client(handler) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=public_resolver,
            robots_checker=allow_robots,
            timeout=0.03,
        )

        with pytest.raises(FetchFailure) as exc:
            await fetcher.fetch("https://example.com/job")

    assert exc.value.code == "fetch_timeout"
    assert exc.value.retryable is True
    assert stream.closed is True


async def test_streams_html_and_returns_hash_and_sanitized_urls() -> None:
    body = "<h1>AI PM</h1>"

    def handler(request: httpx.Request) -> httpx.Response:
        return html_response(body)

    async with make_client(handler) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=public_resolver,
            robots_checker=allow_robots,
            max_response_bytes=1_000_000,
        )
        page = await fetcher.fetch(
            "https://example.com/job?token=secret&keep=yes"
            "&x-amz-signature=hidden"
        )

    assert page.content_type == "text/html"
    assert page.text == body
    assert len(page.content_sha256) == 64
    assert page.source_url == "https://example.com/job?keep=yes"
    assert page.final_url == "https://example.com/job?keep=yes"


async def test_unknown_declared_charset_falls_back_safely() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain; charset=not-a-codec"},
            stream=StaticStream(b"AI product manager"),
        )

    async with make_client(handler) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=public_resolver,
            robots_checker=allow_robots,
        )
        page = await fetcher.fetch("https://example.com/job")

    assert page.text == "AI product manager"


@pytest.mark.parametrize(
    "pseudo_charset",
    ["base64_codec", "hex_codec", "rot_13"],
)
async def test_non_text_codec_charset_falls_back_safely(
    pseudo_charset: str,
) -> None:
    body = "AI 产品经理".encode("utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-type": (
                    f"text/plain; charset={pseudo_charset}"
                )
            },
            stream=StaticStream(body),
        )

    async with make_client(handler) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=public_resolver,
            robots_checker=allow_robots,
        )
        page = await fetcher.fetch("https://example.com/job")

    assert page.text == "AI 产品经理"


async def test_decodes_gbk_from_early_html_meta_charset() -> None:
    expected = "人工智能产品经理"
    body = (
        f'<html><head><meta charset="gbk"></head>'
        f"<body><h1>{expected}</h1></body></html>"
    ).encode("gbk")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            stream=StaticStream(body),
        )

    async with make_client(handler) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=public_resolver,
            robots_checker=allow_robots,
        )
        page = await fetcher.fetch("https://example.com/job")

    assert expected in page.text
    assert "�" not in page.text


async def test_valid_http_charset_takes_priority_over_html_meta() -> None:
    expected = "产品经理"
    body = (
        '<meta charset="gbk">'
        f"<h1>{expected}</h1>"
    ).encode("utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            stream=StaticStream(body),
        )

    async with make_client(handler) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=public_resolver,
            robots_checker=allow_robots,
        )
        page = await fetcher.fetch("https://example.com/job")

    assert expected in page.text


async def test_bom_takes_priority_when_http_charset_is_absent() -> None:
    expected = "AI 产品经理"
    body = expected.encode("utf-16")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            stream=StaticStream(body),
        )

    async with make_client(handler) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=public_resolver,
            robots_checker=allow_robots,
        )
        page = await fetcher.fetch("https://example.com/job")

    assert page.text == expected


def test_sanitize_public_url_removes_sensitive_keys_case_insensitively() -> None:
    sanitized = sanitize_public_url(
        "https://example.com/job?API_KEY=a&signature=b&auth=c"
        "&Authorization=d&safe=e#fragment"
    )

    assert sanitized == "https://example.com/job?safe=e"


@pytest.mark.parametrize(
    "sensitive_key",
    [
        "client_secret",
        "refresh_token",
        "id_token",
        "password",
        "credential",
        "X-Amz-Security-Token",
        "X-Amz-Credential",
        "X-Amz-Signature",
        "x-ms-signature",
        "sig",
        "skoid",
        "API%2DKey",
        "CLIENT%255FSECRET",
        "x-api-key",
        "access_key",
        "subscription-key",
        "AWSAccessKeyId",
        "X-Goog-Api-Key",
        "X-Goog-Algorithm",
        "GoogleAccessId",
    ],
)
def test_sanitize_public_url_removes_sensitive_key_families(
    sensitive_key: str,
) -> None:
    sanitized = sanitize_public_url(
        f"https://example.com/job?safe=1&{sensitive_key}=hidden"
        f"&{sensitive_key}=again&keep=2"
    )

    assert sanitized == "https://example.com/job?safe=1&keep=2"


def test_sanitize_public_url_preserves_benign_key_suffix_words() -> None:
    sanitized = sanitize_public_url(
        "https://example.com/job?keyword=ai&monkey=capuchin"
        "&hockey=field&safe=1"
    )

    assert sanitized == (
        "https://example.com/job?keyword=ai&monkey=capuchin"
        "&hockey=field&safe=1"
    )


def test_sanitize_public_url_handles_semicolon_separators() -> None:
    sanitized = sanitize_public_url(
        "https://example.com/job?safe=1;token=secret;"
        "client_secret=hidden;keep=2"
    )

    assert sanitized == "https://example.com/job?safe=1&keep=2"


async def test_owned_transport_requires_peer_metadata() -> None:
    async with make_client(lambda request: html_response()) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=public_resolver,
            robots_checker=allow_robots,
            require_peer_metadata=True,
        )

        with pytest.raises(FetchFailure) as exc:
            await fetcher.fetch("https://example.com/job")

    assert exc.value.code == "unsafe_url"


async def test_required_peer_metadata_accepts_exact_validated_peer() -> None:
    class PublicPeer:
        def get_extra_info(self, name: str):
            assert name == "server_addr"
            return (str(PUBLIC_IPV4), 443)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            stream=StaticStream(b"<h1>AI PM</h1>"),
            extensions={"network_stream": PublicPeer()},
        )

    async with make_client(handler) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=public_resolver,
            robots_checker=allow_robots,
            require_peer_metadata=True,
        )
        page = await fetcher.fetch("https://example.com/job")

    assert page.status_code == 200


async def test_from_config_disables_environment_proxy_and_owns_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    client = make_client(lambda request: html_response())

    def client_factory(**kwargs: object) -> httpx.AsyncClient:
        captured.update(kwargs)
        return client

    monkeypatch.setattr(fetcher_module.httpx, "AsyncClient", client_factory)
    fetcher = SafeWebFetcher.from_config(JobDescriptionToolConfig())

    assert captured["follow_redirects"] is False
    assert captured["trust_env"] is False
    assert fetcher.require_peer_metadata is True

    await fetcher.aclose()

    assert client.is_closed
