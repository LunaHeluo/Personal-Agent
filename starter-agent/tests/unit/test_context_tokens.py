import json

from starter_agent.agent.token_counter import TokenCounter
from starter_agent.agent.tool_result_guard import ToolResultGuard
from starter_agent.domain.models import Message


def test_token_counter_counts_complete_tool_message() -> None:
    counter = TokenCounter(safety_ratio=1.15)
    content_only = counter.text("悉尼 AI Agent 工程师").tokens
    tool_message = counter.tool_message(
        '{"jobs":[{"title":"AI Agent Engineer"}]}',
        "search_jobs_serpapi",
        "call-1",
    ).tokens

    assert content_only > 0
    assert tool_message > content_only
    assert counter.messages([Message(role="user", content="你好")]).estimated


def test_tool_result_guard_returns_traceable_partial_result() -> None:
    counter = TokenCounter(safety_ratio=1.15)
    guard = ToolResultGuard(counter, max_result_tokens=300)
    raw = json.dumps(
        {
            "ok": True,
            "data": {
                "jobs": [
                    {"title": f"AI Agent Engineer {index}", "description": "岗位详情" * 100}
                    for index in range(30)
                ]
            },
        },
        ensure_ascii=False,
    )

    result = guard.guard(
        raw,
        "search_jobs_serpapi",
        "call-1",
        "tool:search_jobs_serpapi:turn-1:call-1",
    )
    payload = json.loads(result.content)

    assert result.is_truncated is True
    assert result.raw_result_tokens > result.context_result_tokens
    assert payload["metadata"]["is_truncated"] is True
    assert payload["metadata"]["original_count"] == 30
    assert payload["metadata"]["returned_count"] < 30
    assert payload["metadata"]["omitted_count"] > 0
    assert payload["metadata"]["has_more"] is True
    assert payload["metadata"]["raw_source_ref"].startswith("tool:")
    assert payload["metadata"]["continuation_hint"]
