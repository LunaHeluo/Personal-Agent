from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from starter_agent.agent.token_counter import TokenCounter
from starter_agent.agent.tool_result_guard import ToolResultGuard
from starter_agent.tools.adapters.safe_web_fetcher import (
    FetchFailure,
    SafeWebFetcher,
)
from tests.integration.test_capabilities_api import (
    test_builtin_enable_override_is_cas_persistent_and_review_is_stable_4xx as _api_failure_case,
)
from tests.integration.test_capability_ui_api_contract import (
    test_mutations_use_cas_loading_locks_and_authoritative_rereads as _ui_failure_case,
)
from tests.unit.test_safe_web_fetcher import (
    allow_robots,
    make_client,
    public_resolver,
)
from tests.unit.test_tool_governance_matrix import _boundary


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "request_changes", "reason"),
    [
        (
            {
                "name": "server-unavailable",
                "connection_state": "closed",
                "effect": "allowlist_auto",
            },
            {},
            "server_not_connected",
        ),
        (
            {"name": "tool-missing", "effect": "allowlist_auto"},
            {"tool_name": "missing_tool"},
            "tool_not_found",
        ),
        (
            {
                "name": "schema-invalid",
                "effect": "allowlist_auto",
                "arguments": {
                    "url": "https://jobs.example.com/opening",
                    "timeout": 31,
                },
            },
            {},
            "invalid_arguments",
        ),
    ],
    ids=["server-unavailable", "tool-missing", "schema-invalid"],
)
async def test_unavailable_missing_and_invalid_calls_fail_before_invoker(
    case: dict[str, Any],
    request_changes: dict[str, Any],
    reason: str,
) -> None:
    gate, _executor, request, invocations = _boundary(case)
    if request_changes:
        request = request.model_copy(update=request_changes)

    decision = await gate.evaluate(request)

    assert (decision.outcome, decision.reason_code) == ("deny", reason)
    assert decision.permit is None
    assert invocations == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler", "reason", "retryable"),
    [
        (lambda _request: httpx.Response(403), "access_blocked", False),
        (
            lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=request)),
            "fetch_timeout",
            True,
        ),
    ],
    ids=["page-refused", "browser-timeout"],
)
async def test_page_refusal_and_browser_timeout_are_typed_degradations(
    handler,
    reason: str,
    retryable: bool,
) -> None:
    async with make_client(handler) as client:
        fetcher = SafeWebFetcher(
            client=client,
            resolver=public_resolver,
            robots_checker=allow_robots,
        )

        with pytest.raises(FetchFailure) as raised:
            await fetcher.fetch("https://jobs.example.com/opening")

    assert raised.value.code == reason
    assert raised.value.retryable is retryable


def test_oversized_result_is_redacted_but_keeps_source_reference_and_url() -> None:
    raw_source_ref = "tool:browser_read:turn-1:call-1"
    source_url = "https://jobs.example.com/opening/42"
    raw = json.dumps(
        {
            "ok": True,
            "data": {
                "authorization": "Bearer TOP-SECRET-TOKEN",
                "description": "Build trustworthy agents. " * 1_000,
            },
            "metadata": {"source_url": source_url},
        }
    )
    guarded = ToolResultGuard(
        TokenCounter(safety_ratio=1),
        max_result_tokens=150,
    ).guard(raw, "browser_read", "call-1", raw_source_ref)
    payload = json.loads(guarded.content)

    assert guarded.is_truncated is True
    assert guarded.truncation_reason == "token_budget"
    assert "TOP-SECRET-TOKEN" not in guarded.redacted_content
    assert payload["metadata"]["raw_source_ref"] == raw_source_ref
    assert payload["metadata"]["source_url"] == source_url


def test_failed_api_and_ui_operations_reconcile_authoritative_state() -> None:
    _api_failure_case()
    _ui_failure_case()
