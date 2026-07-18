from uuid import uuid4

import httpx
import pytest

from starter_agent.tools.base import ToolContext
from starter_agent.tools.builtin.job_search import (
    SearchJobsSerpApiTool,
    sanitize_url,
)


class FakeResponse:
    def __init__(self, payload: object, status_code: int = 200):
        self.payload = payload
        request = httpx.Request("GET", "https://serpapi.com/search.json")
        self.response = httpx.Response(status_code, request=request)

    def raise_for_status(self) -> None:
        self.response.raise_for_status()

    def json(self) -> object:
        return self.payload


class FakeClient:
    def __init__(self, responses: list[object]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def get(self, url: str, *, params: dict, timeout: float):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def context() -> ToolContext:
    return ToolContext(session_id=uuid4(), turn_id=uuid4())


async def test_missing_key_is_safe_and_does_not_call_provider() -> None:
    client = FakeClient([])
    tool = SearchJobsSerpApiTool(
        key_resolver=lambda: ("backup", None, "SERPAPI_API_KEY_BACKUP"),
        client=client,
    )

    result = await tool.execute({"query": "AI Agent jobs"}, context())

    assert not result.ok
    assert result.error_code == "missing_api_key"
    assert result.metadata == {
        "api_key_profile": "backup",
        "api_key_env": "SERPAPI_API_KEY_BACKUP",
    }
    assert client.calls == []


async def test_google_jobs_result_has_source_time_and_no_secret() -> None:
    secret = "unit-test-secret"
    client = FakeClient(
        [
            FakeResponse(
                {
                    "jobs_results": [
                        {
                            "title": "AI Agent Engineer",
                            "company_name": "Example",
                            "location": "Sydney NSW",
                            "share_link": (
                                "https://jobs.example/1?token=leak&ref=public#fragment"
                            ),
                            "description": "Build agent systems",
                        }
                    ]
                }
            )
        ]
    )
    tool = SearchJobsSerpApiTool(
        key_resolver=lambda: ("primary", secret, "SERPAPI_API_KEY"),
        client=client,
    )

    result = await tool.execute(
        {"query": "AI Agent engineer jobs", "location": "Sydney", "limit": 1},
        context(),
    )

    assert result.ok
    assert result.data["search_engine"] == "google_jobs"
    assert result.data["results"][0]["source"] == "serpapi_google_jobs"
    assert result.data["results"][0]["retrieved_at"]
    assert result.data["results"][0]["url"] == "https://jobs.example/1?ref=public"
    assert secret not in result.model_dump_json()


async def test_empty_google_jobs_falls_back_to_google() -> None:
    client = FakeClient(
        [
            FakeResponse({"jobs_results": []}),
            FakeResponse(
                {
                    "organic_results": [
                        {
                            "title": "AI Engineer - Sydney",
                            "link": "https://example.com/careers/1",
                            "snippet": "Agent engineering role",
                        }
                    ]
                }
            ),
        ]
    )
    tool = SearchJobsSerpApiTool(
        key_resolver=lambda: ("primary", "secret", "SERPAPI_API_KEY"),
        client=client,
    )

    result = await tool.execute(
        {"query": "AI Agent engineer jobs", "location": "Sydney", "limit": 3},
        context(),
    )

    assert result.ok
    assert len(client.calls) == 2
    assert client.calls[0]["params"]["engine"] == "google_jobs"
    assert client.calls[1]["params"]["engine"] == "google"
    assert result.data["search_engine"] == "google"
    assert result.data["results"][0]["source"] == "serpapi_google"


async def test_google_jobs_no_results_error_falls_back_to_google() -> None:
    client = FakeClient(
        [
            FakeResponse(
                {"error": "Google hasn't returned any results for this query."}
            ),
            FakeResponse(
                {
                    "organic_results": [
                        {
                            "title": "AI Agent Engineer Sydney",
                            "link": "https://example.com/jobs/agent",
                            "snippet": "Sydney role",
                        }
                    ]
                }
            ),
        ]
    )
    tool = SearchJobsSerpApiTool(
        key_resolver=lambda: ("primary", "secret", "SERPAPI_API_KEY"),
        client=client,
    )

    result = await tool.execute(
        {"query": "AI Agent engineer jobs", "location": "Sydney"}, context()
    )

    assert result.ok
    assert len(client.calls) == 2
    assert result.data["search_engine"] == "google"


async def test_both_searches_empty_returns_no_results() -> None:
    client = FakeClient(
        [FakeResponse({"jobs_results": []}), FakeResponse({"organic_results": []})]
    )
    tool = SearchJobsSerpApiTool(
        key_resolver=lambda: ("primary", "secret", "SERPAPI_API_KEY"),
        client=client,
    )

    result = await tool.execute({"query": "AI Agent jobs"}, context())

    assert not result.ok
    assert result.error_code == "no_results"
    assert len(client.calls) == 2


@pytest.mark.parametrize("limit", [0, 11, -1, True, "5"])
async def test_invalid_limit_is_rejected(limit) -> None:
    tool = SearchJobsSerpApiTool(
        key_resolver=lambda: ("primary", "secret", "SERPAPI_API_KEY"),
        client=FakeClient([]),
    )
    result = await tool.execute(
        {"query": "AI Agent jobs", "limit": limit}, context()
    )
    assert result.error_code == "invalid_arguments"


@pytest.mark.parametrize(
    ("status", "error_code"),
    [(401, "authentication_failed"), (403, "authentication_failed"), (429, "rate_limited")],
)
async def test_http_errors_are_classified(status, error_code) -> None:
    tool = SearchJobsSerpApiTool(
        key_resolver=lambda: ("primary", "secret", "SERPAPI_API_KEY"),
        client=FakeClient([FakeResponse({}, status_code=status)]),
    )
    result = await tool.execute({"query": "AI Agent jobs"}, context())
    assert result.error_code == error_code


async def test_provider_quota_error_is_classified() -> None:
    tool = SearchJobsSerpApiTool(
        key_resolver=lambda: ("primary", "secret", "SERPAPI_API_KEY"),
        client=FakeClient([FakeResponse({"error": "Your plan has no credits"})]),
    )
    result = await tool.execute({"query": "AI Agent jobs"}, context())
    assert result.error_code == "quota_exceeded"


async def test_transient_timeout_is_retried_then_succeeds() -> None:
    request = httpx.Request("GET", "https://serpapi.com/search.json")
    client = FakeClient(
        [
            httpx.ReadTimeout("temporary timeout", request=request),
            FakeResponse(
                {
                    "jobs_results": [
                        {
                            "title": "AI Engineer",
                            "company_name": "Example",
                            "location": "Shanghai",
                            "share_link": "https://example.com/job/1",
                        }
                    ]
                }
            ),
        ]
    )
    tool = SearchJobsSerpApiTool(
        key_resolver=lambda: ("primary", "secret", "SERPAPI_API_KEY"),
        client=client,
        max_retries=1,
        retry_backoff_seconds=0,
    )

    result = await tool.execute(
        {"query": "AI jobs", "location": "Shanghai"}, context()
    )

    assert result.ok
    assert len(client.calls) == 2


async def test_connection_failure_has_actionable_error_after_retry() -> None:
    request = httpx.Request("GET", "https://serpapi.com/search.json")
    client = FakeClient(
        [
            httpx.ConnectError("connection failed", request=request),
            httpx.ConnectError("connection failed", request=request),
        ]
    )
    tool = SearchJobsSerpApiTool(
        key_resolver=lambda: ("primary", "secret", "SERPAPI_API_KEY"),
        client=client,
        max_retries=1,
        retry_backoff_seconds=0,
    )

    result = await tool.execute({"query": "AI jobs"}, context())

    assert result.error_code == "search_connection_failed"
    assert result.retryable is True
    assert result.metadata["failure_type"] == "connection_failed"
    assert result.metadata["attempts"] == 2
    assert "网络" in result.display


async def test_invalid_provider_response_is_classified() -> None:
    tool = SearchJobsSerpApiTool(
        key_resolver=lambda: ("primary", "secret", "SERPAPI_API_KEY"),
        client=FakeClient([FakeResponse(["not", "an", "object"])]),
        retry_backoff_seconds=0,
    )

    result = await tool.execute({"query": "AI jobs"}, context())

    assert result.error_code == "invalid_response"
    assert result.metadata["failure_type"] == "invalid_response"


def test_sanitize_url_rejects_non_http_and_removes_sensitive_values() -> None:
    assert sanitize_url("javascript:alert(1)") == ""
    assert (
        sanitize_url("https://example.test/job?api_key=x&ref=y#secret")
        == "https://example.test/job?ref=y"
    )
