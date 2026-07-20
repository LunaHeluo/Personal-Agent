from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from starter_agent.tools.adapters.job_description_extractor import (
    ExtractedJobDescription,
)
from starter_agent.tools.adapters.safe_web_fetcher import (
    FetchFailure,
    FetchedPage,
)
from starter_agent.tools.base import ToolContext
from starter_agent.tools.builtin.job_description_search import (
    SearchJobDescriptionTool,
)


class FakeFetcher:
    def __init__(self, result: FetchedPage | FetchFailure) -> None:
        self.result = result
        self.requested_urls: list[str] = []

    async def fetch(self, url: str) -> FetchedPage:
        self.requested_urls.append(url)
        if isinstance(self.result, FetchFailure):
            raise self.result
        return self.result


class FakeExtractor:
    def __init__(self, result: ExtractedJobDescription) -> None:
        self.result = result
        self.inputs: list[tuple[str, str]] = []

    def extract(self, content: str, content_type: str) -> ExtractedJobDescription:
        self.inputs.append((content, content_type))
        return self.result


def context() -> ToolContext:
    return ToolContext(session_id=uuid4(), turn_id=uuid4())


def page() -> FetchedPage:
    return FetchedPage(
        source_url="https://example.com/job",
        final_url="https://example.com/job",
        status_code=200,
        content_type="text/html",
        text="<h1>AI PM</h1>",
        content_sha256="a" * 64,
    )


def extracted(**overrides: object) -> ExtractedJobDescription:
    values: dict[str, object] = {
        "title": "AI Product Manager",
        "company": "Example",
        "responsibilities": ["Own roadmap."],
        "requirements": ["Product experience."],
        "raw_text": "AI Product Manager Own roadmap. Product experience.",
        "completeness": "complete",
        "extraction_method": "html",
    }
    values.update(overrides)
    return ExtractedJobDescription(**values)  # type: ignore[arg-type]


def make_tool(
    *,
    fetched: FetchedPage | FetchFailure | None = None,
    parsed: ExtractedJobDescription | None = None,
) -> tuple[SearchJobDescriptionTool, FakeFetcher, FakeExtractor]:
    fetcher = FakeFetcher(fetched or page())
    extractor = FakeExtractor(parsed or extracted())
    return SearchJobDescriptionTool(fetcher, extractor), fetcher, extractor


async def test_returns_traceable_complete_job() -> None:
    tool, fetcher, extractor = make_tool()

    result = await tool.execute(
        {
            "url": "https://example.com/job",
            "expected_title": "AI Product Manager",
            "expected_company": "Example",
            "source_ref": "tool:search_jobs_serpapi:turn:call",
        },
        context(),
    )

    assert result.ok
    assert fetcher.requested_urls == ["https://example.com/job"]
    assert extractor.inputs == [("<h1>AI PM</h1>", "text/html")]
    assert result.data["title"] == "AI Product Manager"
    assert result.data["completeness"] == "complete"
    assert result.data["source_url"] == "https://example.com/job"
    assert result.data["final_url"] == "https://example.com/job"
    assert result.data["content_sha256"] == "a" * 64
    retrieved_at = datetime.fromisoformat(result.data["retrieved_at"])
    assert retrieved_at.tzinfo is not None
    assert retrieved_at.utcoffset() == timedelta(0)
    assert result.metadata == {
        "source_ref": "tool:search_jobs_serpapi:turn:call",
        "fetch_status": "fetched",
        "is_untrusted_external_content": True,
    }
    assert "save" not in result.data
    assert "memory" not in result.data


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"url": ""},
        {"url": "  https://example.com/job"},
        {"url": 1},
        {"url": "https://example.com/job", "unexpected": "value"},
        {"url": "https://example.com/job", "expected_title": 2},
        {"url": "https://example.com/job", "source_ref": []},
    ],
)
async def test_rejects_invalid_arguments(arguments: dict[str, object]) -> None:
    tool, fetcher, _ = make_tool()

    result = await tool.execute(arguments, context())

    assert not result.ok
    assert result.error_code == "invalid_arguments"
    assert fetcher.requested_urls == []


@pytest.mark.parametrize(
    ("code", "retryable"),
    [
        ("unsafe_url", False),
        ("robots_blocked", False),
        ("authentication_required", False),
        ("access_blocked", False),
        ("job_not_found", False),
        ("rate_limited", True),
        ("fetch_timeout", True),
        ("unsupported_content_type", False),
        ("response_too_large", False),
        ("fetch_failed", True),
    ],
)
async def test_maps_all_stable_fetch_failures_without_losing_retryability(
    code: str,
    retryable: bool,
) -> None:
    tool, _, _ = make_tool(
        fetched=FetchFailure(code, "Source unavailable", retryable=retryable)
    )

    result = await tool.execute({"url": "https://example.com/job"}, context())

    assert not result.ok
    assert result.error_code == code
    assert result.retryable is retryable
    assert result.metadata == {
        "source_ref": "",
        "fetch_status": "failed",
        "is_untrusted_external_content": True,
    }


async def test_rejects_selected_title_that_does_not_match_fetched_job() -> None:
    tool, _, _ = make_tool()

    result = await tool.execute(
        {"url": "https://example.com/job", "expected_title": "Data Engineer"},
        context(),
    )

    assert not result.ok
    assert result.error_code == "job_mismatch"


async def test_rejects_selected_company_that_does_not_match_fetched_job() -> None:
    tool, _, _ = make_tool()

    result = await tool.execute(
        {"url": "https://example.com/job", "expected_company": "Other Corp"},
        context(),
    )

    assert not result.ok
    assert result.error_code == "job_mismatch"


async def test_rejects_dynamic_empty_shell() -> None:
    tool, _, _ = make_tool(parsed=extracted(raw_text="", title=""))

    result = await tool.execute({"url": "https://example.com/job"}, context())

    assert not result.ok
    assert result.error_code == "dynamic_page_unsupported"


async def test_rejects_description_without_core_sections() -> None:
    tool, _, _ = make_tool(
        parsed=extracted(
            responsibilities=[],
            requirements=[],
            raw_text="AI Product Manager at Example",
            completeness="unverified",
        )
    )

    result = await tool.execute({"url": "https://example.com/job"}, context())

    assert not result.ok
    assert result.error_code == "incomplete_job_description"


@pytest.mark.parametrize(
    ("expected", "actual", "matches"),
    [
        ("AI", "Paid Social Manager", False),
        ("AB", "Grab Holdings", False),
        ("Example Inc", "Example, Inc.", True),
        ("C++ Developer", "C Developer", False),
        ("C++ Developer", "C++-Developer", True),
        ("C Developer", "C++/C Developer", False),
        ("C Developer", "C Developer", True),
        ("AI Product Manager", "Senior AI Product Manager", True),
        ("Senior AI Product Manager", "AI Product Manager", True),
        (
            "\uff25\uff58\uff41\uff4d\uff50\uff4c\uff45 \uff29\uff4e\uff43",
            "example, inc.",
            True,
        ),
    ],
)
def test_matcher_uses_unicode_normalized_token_boundaries(
    expected: str,
    actual: str,
    matches: bool,
) -> None:
    assert SearchJobDescriptionTool._contains_match(expected, actual) is matches


async def test_unexpected_extractor_exception_is_not_mislabeled_as_fetch_failure() -> None:
    class RaisingExtractor:
        def extract(self, content: str, content_type: str) -> ExtractedJobDescription:
            raise RuntimeError("parser defect")

    tool = SearchJobDescriptionTool(  # type: ignore[arg-type]
        FakeFetcher(page()), RaisingExtractor()
    )

    with pytest.raises(RuntimeError, match="parser defect"):
        await tool.execute({"url": "https://example.com/job"}, context())
