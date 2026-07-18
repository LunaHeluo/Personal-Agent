from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from openai import (
    APIStatusError,
    AsyncOpenAI,
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)

from starter_agent.domain.errors import (
    ProviderAuthenticationError,
    ProviderConnectionError,
    ProviderContentBlockedError,
    ProviderContextLengthError,
    ProviderError,
    ProviderInvalidRequestError,
    ProviderModelUnavailableError,
    ProviderPermissionDeniedError,
    ProviderQuotaExceededError,
    ProviderRateLimitError,
    ProviderServiceUnavailableError,
    ProviderTimeoutError,
)
from starter_agent.domain.models import Message, ModelResponse, ToolCall
from starter_agent.providers.base import Provider


class OpenAICompatibleProvider(Provider):
    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str,
        timeout: float,
        max_retries: int,
        temperature: float,
        stream: bool = False,
        thinking: str | None = None,
    ):
        self.name = name
        self.temperature = temperature
        self.stream = stream
        self.thinking = thinking
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
        )

    async def complete(
        self,
        messages: list[Message],
        model: str,
        tools: list[dict[str, Any]],
        on_delta: Callable[[str], Awaitable[None]] | None = None,
        tool_choice: str | None = None,
    ) -> ModelResponse:
        request_messages: list[dict[str, Any]] = []
        for message in messages:
            item: dict[str, Any] = {
                "role": message.role,
                "content": message.content,
            }
            if message.name:
                item["name"] = message.name
            if message.tool_call_id:
                item["tool_call_id"] = message.tool_call_id
            if message.tool_calls:
                item["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments),
                        },
                    }
                    for call in message.tool_calls
                ]
            request_messages.append(item)
        try:
            extra_body = (
                {"thinking": {"type": self.thinking}}
                if self.thinking
                else None
            )
            if self.stream:
                stream = await self.client.chat.completions.create(
                    model=model,
                    messages=request_messages,  # type: ignore[arg-type]
                    tools=tools or None,  # type: ignore[arg-type]
                    tool_choice=_tool_choice(tool_choice),  # type: ignore[arg-type]
                    temperature=self.temperature,
                    stream=True,
                    stream_options={"include_usage": True},
                    extra_body=extra_body,
                )
                content_parts: list[str] = []
                call_parts: dict[int, dict[str, str]] = {}
                usage: dict[str, Any] = {}
                async for chunk in stream:
                    if chunk.usage:
                        usage = chunk.usage.model_dump()
                    for choice_item in chunk.choices:
                        delta = choice_item.delta
                        if delta.content:
                            content_parts.append(delta.content)
                            if on_delta:
                                await on_delta(delta.content)
                        for call in delta.tool_calls or []:
                            part = call_parts.setdefault(
                                call.index,
                                {"id": "", "name": "", "arguments": ""},
                            )
                            if call.id:
                                part["id"] = call.id
                            if call.function:
                                if call.function.name:
                                    part["name"] += call.function.name
                                if call.function.arguments:
                                    part["arguments"] += call.function.arguments
                calls = [
                    ToolCall(
                        id=part["id"] or f"stream-call-{index}",
                        name=part["name"],
                        arguments=json.loads(part["arguments"] or "{}"),
                    )
                    for index, part in sorted(call_parts.items())
                ]
                return ModelResponse(
                    content="".join(content_parts) or None,
                    tool_calls=calls,
                    provider=self.name,
                    model=model,
                    usage=usage,
                )
            response = await self.client.chat.completions.create(
                model=model,
                messages=request_messages,  # type: ignore[arg-type]
                tools=tools or None,  # type: ignore[arg-type]
                tool_choice=_tool_choice(tool_choice),  # type: ignore[arg-type]
                temperature=self.temperature,
                extra_body=extra_body,
            )
        except AuthenticationError as exc:
            raise ProviderAuthenticationError(
                status=401, provider=self.name, model=model
            ) from exc
        except RateLimitError as exc:
            error_type = _classify_status_error(exc)
            raise error_type(status=exc.status_code, provider=self.name, model=model) from exc
        except APITimeoutError as exc:
            raise ProviderTimeoutError(provider=self.name, model=model) from exc
        except APIConnectionError as exc:
            raise ProviderConnectionError(provider=self.name, model=model) from exc
        except APIStatusError as exc:
            error_type = _classify_status_error(exc)
            raise error_type(status=exc.status_code, provider=self.name, model=model) from exc
        except Exception as exc:
            raise ProviderError(
                "模型服务请求失败",
                suggestion="请稍后重试；如问题持续，请检查服务端日志",
                provider=self.name,
                model=model,
            ) from exc

        choice = response.choices[0].message
        if choice.content and on_delta:
            await on_delta(choice.content)
        calls = [
            ToolCall(
                id=call.id,
                name=call.function.name,
                arguments=json.loads(call.function.arguments or "{}"),
            )
            for call in (choice.tool_calls or [])
        ]
        usage = response.usage.model_dump() if response.usage else {}
        return ModelResponse(
            content=choice.content,
            tool_calls=calls,
            provider=self.name,
            model=model,
            usage=usage,
        )

    async def health(self, model: str) -> tuple[bool, str]:
        try:
            await self.complete(
                [Message(role="user", content="Reply with OK.")],
                model=model,
                tools=[],
            )
            return True, f"{self.name} responded successfully ({model})"
        except ProviderError as exc:
            return False, str(exc)


def _classify_status_error(exc: APIStatusError) -> type[ProviderError]:
    """Map provider responses without exposing their raw, potentially sensitive body."""
    status = exc.status_code
    body = getattr(exc, "body", None)
    searchable = f"{exc} {json.dumps(body, ensure_ascii=False, default=str)}".lower()
    if status == 401:
        return ProviderAuthenticationError
    if status == 403:
        if any(word in searchable for word in ("safety", "moderation", "content policy")):
            return ProviderContentBlockedError
        return ProviderPermissionDeniedError
    if status == 429:
        if any(word in searchable for word in ("quota", "balance", "credit", "insufficient", "billing")):
            return ProviderQuotaExceededError
        return ProviderRateLimitError
    if status in {500, 502, 503, 504}:
        return ProviderServiceUnavailableError
    if status == 413 or any(word in searchable for word in ("context_length", "context length", "maximum context", "too many tokens")):
        return ProviderContextLengthError
    if any(word in searchable for word in ("model_not_found", "model not found", "invalid model", "does not exist", "unknown model")):
        return ProviderModelUnavailableError
    if status == 404:
        return ProviderModelUnavailableError
    if any(word in searchable for word in ("safety", "moderation", "content policy")):
        return ProviderContentBlockedError
    if status == 400:
        return ProviderInvalidRequestError
    return ProviderError


def _tool_choice(name: str | None) -> dict[str, Any] | None:
    if not name:
        return None
    return {"type": "function", "function": {"name": name}}
