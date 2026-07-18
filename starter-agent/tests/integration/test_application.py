from starter_agent.domain.models import ModelResponse, ToolCall, ToolResult
from starter_agent.providers.mock import MockProvider
from starter_agent.agent.tool_result_guard import ToolResultGuard


async def test_mock_chat_persists_session(application) -> None:
    first = await application.chat("你好", provider_name="mock")
    second = await application.chat(
        "现在几点？",
        session_id=first.session_id,
        provider_name="mock",
    )

    assert first.session_id == second.session_id
    assert second.tool_calls == 1
    messages = application.store.list_messages(first.session_id)
    assert [message.role for message in messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert messages[3].tool_calls[0].name == "get_current_time"
    assert messages[4].name == "get_current_time"


async def test_mock_chat_does_not_invent_token_usage(application) -> None:
    result = await application.chat("hello", provider_name="mock")

    assert result.usage == {}
    assert result.session_usage.total_tokens == 0
    assert result.token_budget_status == "normal"


async def test_tool_completed_event_exposes_only_safe_result_metadata(
    application, monkeypatch
) -> None:
    tool = application.runtime.tools.get("get_current_time")
    assert tool is not None

    async def execute_with_metadata(arguments, context):
        return ToolResult(
            ok=True,
            data={"value": "not streamed"},
            display="safe display",
            metadata={
                "draft_id": "email-draft:test",
                "content_sha256": "a" * 64,
                "sent": False,
            },
        )

    monkeypatch.setattr(tool, "execute", execute_with_metadata)
    events = []

    async def on_tool_event(event):
        events.append(event)

    await application.chat(
        "执行时间工具",
        provider_name="mock",
        required_tool_name="get_current_time",
        on_tool_event=on_tool_event,
    )

    completed = next(
        event for event in events if event["type"] == "tool_completed"
    )
    assert completed["metadata"] == {
        "draft_id": "email-draft:test",
        "content_sha256": "a" * 64,
        "sent": False,
    }
    assert "body_text" not in completed
    assert "credential" not in completed


async def test_provider_usage_is_retained_accumulated_and_warned(
    application, monkeypatch
) -> None:
    async def complete_with_usage(self, messages, model, tools, **kwargs):
        return ModelResponse(
            content="done",
            provider="zhipu",
            model="glm-4.7",
            usage={
                "prompt_tokens": 700,
                "completion_tokens": 200,
                "total_tokens": 900,
            },
        )

    monkeypatch.setattr(MockProvider, "complete", complete_with_usage)
    application.settings.context.max_total_tokens = 1000
    application.settings.context.warning_ratio = 0.8

    first = await application.chat("first", provider_name="mock")
    second = await application.chat(
        "second", session_id=first.session_id, provider_name="mock"
    )

    assert first.usage == {
        "prompt_tokens": 700,
        "completion_tokens": 200,
        "total_tokens": 900,
    }
    assert first.session_usage.total_tokens == 900
    assert first.token_budget_status == "warning"
    assert second.session_usage.prompt_tokens == 1400
    assert second.session_usage.completion_tokens == 400
    assert second.session_usage.total_tokens == 1800
    assert second.token_budget_status == "exceeded"


async def test_history_summary_is_traceable_and_emits_tool_ui_events(
    application,
) -> None:
    application.settings.context.keep_recent_turns = 1
    application.settings.context.history_budget_tokens = 100000
    events = []

    first = await application.chat("第一轮历史内容" * 500, provider_name="mock")

    async def on_tool_event(event):
        events.append(event)

    second = await application.chat(
        "第二轮",
        session_id=first.session_id,
        provider_name="mock",
        on_tool_event=on_tool_event,
    )

    assert second.summary_trace is not None
    assert second.summary_trace.before_tokens > second.summary_trace.after_tokens
    assert second.summary_trace.source_message_ids
    assert second.tool_calls == 1
    assert events[0]["type"] == "tool_started"
    assert events[0]["name"] == "summarize_context"
    assert events[-1]["type"] == "tool_completed"
    assert events[-1]["ok"] is True
    assert events[-1]["summary_id"] == str(second.summary_trace.summary_id)
    assert "summary前 tokens=" in events[-1]["display"]
    assert "summary后 tokens=" in events[-1]["display"]

    stored = application.store.latest_context_summary(first.session_id)
    assert stored is not None
    assert stored.id == second.summary_trace.summary_id
    assert application.store.list_messages(first.session_id)[0].content.startswith(
        "第一轮历史内容"
    )


def test_token_calibration_uses_model_specific_safe_coefficient(application) -> None:
    coefficient = application.store.update_token_calibration(
        "zhipu", "glm-4.7", raw_estimate=1000, actual_prompt=1200
    )

    assert coefficient == 1.2
    assert application.store.token_correction_coefficient(
        "zhipu", "glm-4.7"
    ) == 1.2
    assert application.store.token_correction_coefficient(
        "zhipu", "glm-5.1"
    ) == 1.0


async def test_disabling_tool_governance_skips_result_guard(
    application, monkeypatch
) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("ToolResultGuard must not run when governance is off")

    monkeypatch.setattr(ToolResultGuard, "guard", fail_if_called)

    result = await application.chat(
        "执行时间工具",
        provider_name="mock",
        required_tool_name="get_current_time",
        tool_governance_enabled=False,
    )

    assert result.tool_calls == 1
    assert result.tool_governance_enabled is False


async def test_max_model_calls_returns_recoverable_continuation(
    application, monkeypatch
) -> None:
    calls = 0

    async def tool_loop_until_continued(self, messages, model, tools, **kwargs):
        nonlocal calls
        calls += 1
        if any(
            message.role == "user" and message.content.startswith("请继续完成")
            for message in messages
        ):
            return ModelResponse(
                content="已基于上一轮工具结果完成回答",
                provider="mock",
                model="starter-mock",
                usage={"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            )
        return ModelResponse(
            provider="mock",
            model="starter-mock",
            tool_calls=[
                ToolCall(
                    id=f"call-{calls}",
                    name="get_current_time",
                    arguments={"timezone": "UTC" if calls % 2 else "+00:00"},
                )
            ],
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )

    monkeypatch.setattr(MockProvider, "complete", tool_loop_until_continued)
    application.runtime.budget.max_model_calls = 2
    application.runtime.budget.max_tool_calls = 4

    interrupted = await application.chat("完成一个多步骤任务", provider_name="mock")

    assert interrupted.finish_reason == "continuation_required"
    assert interrupted.continuation is not None
    assert interrupted.continuation.reason == "max_model_calls"
    assert interrupted.continuation.model_calls == 2
    assert interrupted.continuation.tool_calls == 2
    stored = application.store.list_messages(interrupted.session_id)
    assert [message.role for message in stored] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
        "assistant",
    ]
    assert stored[1].tool_calls[0].id == "call-1"
    assert stored[3].tool_calls[0].id == "call-2"

    completed = await application.chat(
        interrupted.continuation.next_message,
        session_id=interrupted.session_id,
        provider_name="mock",
    )

    assert completed.finish_reason == "completed"
    assert completed.continuation is None
    assert completed.content == "已基于上一轮工具结果完成回答"
    assert completed.tool_calls == 0
