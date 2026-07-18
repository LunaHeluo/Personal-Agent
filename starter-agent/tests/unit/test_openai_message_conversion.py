import json

from starter_agent.domain.models import Message, ModelResponse, ToolCall
from starter_agent.providers.openai_compatible import OpenAICompatibleProvider


async def test_tool_call_is_returned_to_provider(monkeypatch) -> None:
    captured = {}

    class FakeCompletions:
        async def create(self, **kwargs):
            captured.update(kwargs)

            class MessageResult:
                content = "done"
                tool_calls = []

            class Choice:
                message = MessageResult()

            class Response:
                choices = [Choice()]
                usage = None

            return Response()

    provider = OpenAICompatibleProvider(
        name="test",
        base_url="https://example.test/v1",
        api_key="not-a-real-key",
        timeout=1,
        max_retries=0,
        temperature=0,
    )
    monkeypatch.setattr(provider.client.chat, "completions", FakeCompletions())
    await provider.complete(
        [
            Message(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="get_current_time",
                        arguments={"timezone": "UTC"},
                    )
                ],
            ),
            Message(
                role="tool",
                content='{"ok": true}',
                name="get_current_time",
                tool_call_id="call-1",
            ),
        ],
        model="test-model",
        tools=[],
        tool_choice="get_current_time",
    )

    tool_call = captured["messages"][0]["tool_calls"][0]
    assert tool_call["id"] == "call-1"
    assert json.loads(tool_call["function"]["arguments"]) == {"timezone": "UTC"}
    assert captured["tool_choice"] == {
        "type": "function",
        "function": {"name": "get_current_time"},
    }


def test_model_response_accepts_nested_provider_usage() -> None:
    response = ModelResponse(
        content="OK",
        provider="zhipu",
        model="glm-5.2",
        usage={
            "prompt_tokens": 10,
            "prompt_tokens_details": {"cached_tokens": 0},
        },
    )

    assert response.usage["prompt_tokens_details"] == {"cached_tokens": 0}
