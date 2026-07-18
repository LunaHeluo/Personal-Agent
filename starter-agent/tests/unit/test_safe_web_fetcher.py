from __future__ import annotations

import ipaddress
from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from starter_agent.tools.adapters.safe_web_fetcher import (
    FetchFailure,
    SafeWebFetcher,
    sanitize_public_url,
)


PUBLIC_IPV4 = ipaddress.ip_address("93.184.216.34")


async def public_resolver(host: str) -> list[ipaddress._BaseAddress]:
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
        content=text.encode(),
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

    async def resolver(host: str) -> list[ipaddress._BaseAddress]:
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
    async def mixed_resolver(host: str) -> list[ipaddress._BaseAddress]:
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


async def test_rejects_azure_platform_ip_from_dns_answer() -> None:
    requests: list[httpx.Request] = []

    async def azure_resolver(host: str) -> list[ipaddress._BaseAddress]:
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
    async def empty_resolver(host: str) -> list[ipaddress._BaseAddress]:
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
                text="User-agent: *\nDisallow: /job\n",
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

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.yielded += 1
            yield chunk

    async def aclose(self) -> None:
        return None


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


async def test_maps_invalid_compressed_stream_to_stable_failure() -> None:
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
    assert exc.value.retryable is True


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
            content=b"AI product manager",
        )

    async with make_client(handler) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=public_resolver,
            robots_checker=allow_robots,
        )
        page = await fetcher.fetch("https://example.com/job")

    assert page.text == "AI product manager"


def test_sanitize_public_url_removes_sensitive_keys_case_insensitively() -> None:
    sanitized = sanitize_public_url(
        "https://example.com/job?API_KEY=a&signature=b&auth=c"
        "&Authorization=d&safe=e#fragment"
    )

    assert sanitized == "https://example.com/job?safe=e"
