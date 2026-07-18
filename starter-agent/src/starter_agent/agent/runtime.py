from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from time import monotonic
from uuid import UUID

from starter_agent.domain.errors import (
    RequiredToolNotCalledError,
    RuntimeBudgetExceeded,
    RuntimeContinuationRequired,
    ToolNotAvailableError,
    ToolPolicyError,
)
from starter_agent.domain.models import Message, ModelResponse
from starter_agent.agent.token_counter import TokenCounter
from starter_agent.agent.tool_result_guard import GuardedToolResult, ToolResultGuard
from starter_agent.observability.logging import get_logger
from starter_agent.providers.base import Provider
from starter_agent.settings import ContextConfig, RuntimeConfig
from starter_agent.tools.base import ToolContext
from starter_agent.tools.policy import ToolPolicy
from starter_agent.tools.registry import ToolRegistry


class AgentRuntime:
    def __init__(
        self,
        tools: ToolRegistry,
        policy: ToolPolicy,
        budget: RuntimeConfig,
        context_config: ContextConfig | None = None,
    ):
        self.tools = tools
        self.policy = policy
        self.budget = budget
        self.context_config = context_config or ContextConfig()
        self.token_counter = TokenCounter(self.context_config.estimator_safety_ratio)
        self.tool_result_guard = ToolResultGuard(
            self.token_counter,
            self.context_config.per_tool_result_tokens,
        )

    async def run(
        self,
        provider: Provider,
        model: str,
        messages: list[Message],
        session_id: UUID,
        turn_id: UUID,
        on_delta: Callable[[str], Awaitable[None]] | None = None,
        required_tool_name: str | None = None,
        on_tool_event: Callable[[dict], Awaitable[None]] | None = None,
        on_tool_artifact: Callable[[dict], Awaitable[None]] | None = None,
        tool_governance_enabled: bool = True,
    ) -> tuple[ModelResponse, list[Message], int]:
        started = monotonic()
        model_calls = 0
        tool_calls = 0
        tool_result_tokens = 0
        repeated_calls: dict[str, int] = {}
        generated: list[Message] = []
        provider_usages: list[dict] = []
        logger = get_logger(session_id=str(session_id), turn_id=str(turn_id))
        if required_tool_name:
            required_tool = self.tools.get(required_tool_name)
            if required_tool is None:
                raise ToolNotAvailableError()
            self.policy.check(required_tool)

        while model_calls < self.budget.max_model_calls:
            if monotonic() - started > self.budget.max_seconds:
                raise RuntimeBudgetExceeded("Maximum run time exceeded")
            model_calls += 1
            logger.info(
                "model.requested",
                provider=provider.name,
                model=model,
                model_call=model_calls,
            )
            response = await provider.complete(
                messages,
                model,
                self.tools.schemas(),
                on_delta=on_delta,
                tool_choice=(required_tool_name if model_calls == 1 else None),
            )
            if response.usage:
                provider_usages.append(response.usage)
            logger.info(
                "model.completed",
                provider=response.provider,
                model=response.model,
                tool_call_count=len(response.tool_calls),
                usage=response.usage,
            )
            if required_tool_name and model_calls == 1:
                if not any(
                    call.name == required_tool_name for call in response.tool_calls
                ):
                    raise RequiredToolNotCalledError()
            if not response.tool_calls:
                if not response.content:
                    response.content = "The model returned an empty response."
                response.usage = aggregate_usage(provider_usages)
                return response, generated, tool_calls

            assistant_tool_message = Message(
                role="assistant",
                content=response.content or "",
                tool_calls=response.tool_calls,
            )
            messages.append(assistant_tool_message)
            generated.append(assistant_tool_message)
            for call in response.tool_calls:
                if on_tool_event:
                    await on_tool_event(
                        {
                            "type": "tool_started",
                            "call_id": call.id,
                            "name": call.name,
                        }
                    )
                if tool_calls >= self.budget.max_tool_calls:
                    raise RuntimeBudgetExceeded("Maximum tool calls exceeded")
                signature = f"{call.name}:{json.dumps(call.arguments, sort_keys=True)}"
                repeated_calls[signature] = repeated_calls.get(signature, 0) + 1
                if repeated_calls[signature] > 2:
                    raise RuntimeBudgetExceeded(
                        f"Repeated identical tool call detected: {call.name}"
                    )
                tool = self.tools.get(call.name)
                tool_ok = False
                tool_error_code: str | None = None
                tool_display = ""
                tool_retryable = False
                tool_failure_type: str | None = None
                tool_metadata: dict[str, object] = {}
                if tool is None:
                    tool_error_code = "unknown_tool"
                    tool_display = "模型请求了未注册的工具"
                    result_text = json.dumps(
                        {"ok": False, "error_code": tool_error_code},
                        ensure_ascii=False,
                    )
                else:
                    try:
                        self.policy.check(tool)
                        logger.info(
                            "tool.requested",
                            tool=tool.name,
                            risk_level=tool.risk_level,
                        )
                        result = await asyncio.wait_for(
                            tool.execute(
                                call.arguments,
                                ToolContext(session_id=session_id, turn_id=turn_id),
                            ),
                            timeout=self.budget.tool_timeout_seconds,
                        )
                        result_text = result.model_dump_json()
                        tool_ok = result.ok
                        tool_error_code = result.error_code
                        tool_display = result.display
                        tool_retryable = result.retryable
                        tool_failure_type = result.metadata.get("failure_type")
                        safe_metadata_keys = {
                            "profile",
                            "draft_id",
                            "content_sha256",
                            "sent",
                            "delivery_mode",
                            "external_delivery",
                            "status",
                            "recipient_count",
                            "sent_at",
                            "message_ref",
                        }
                        tool_metadata = {
                            key: value
                            for key, value in result.metadata.items()
                            if key in safe_metadata_keys
                        }
                        logger.info(
                            "tool.completed",
                            tool=tool.name,
                            ok=result.ok,
                            error_code=result.error_code,
                            tool_governance_enabled=tool_governance_enabled,
                            retryable=result.retryable,
                            failure_type=tool_failure_type,
                        )
                    except ToolPolicyError as exc:
                        tool_error_code = exc.code
                        tool_display = str(exc)
                        result_text = json.dumps(
                            {"ok": False, "error_code": exc.code, "display": str(exc)},
                            ensure_ascii=False,
                        )
                    except TimeoutError:
                        tool_error_code = "tool_timeout"
                        tool_display = "工具执行超过运行时总时限"
                        tool_retryable = True
                        result_text = json.dumps(
                            {"ok": False, "error_code": "tool_timeout"},
                            ensure_ascii=False,
                        )
                    except Exception as exc:
                        tool_error_code = "tool_execution_error"
                        tool_display = "工具执行发生内部错误"
                        result_text = json.dumps(
                            {
                                "ok": False,
                                "error_code": tool_error_code,
                                "display": "工具执行失败",
                            },
                            ensure_ascii=False,
                        )
                        logger.error(
                            "tool.failed",
                            tool=tool.name,
                            error_type=type(exc).__name__,
                        )
                raw_source_ref = f"tool:{call.name}:{turn_id}:{call.id}"
                if tool_governance_enabled:
                    remaining_tool_tokens = max(
                        100,
                        self.context_config.all_tool_results_tokens
                        - tool_result_tokens,
                    )
                    guard = ToolResultGuard(
                        self.token_counter,
                        min(
                            self.context_config.per_tool_result_tokens,
                            remaining_tool_tokens,
                        ),
                    )
                    guarded = guard.guard(
                        result_text,
                        call.name,
                        call.id,
                        raw_source_ref,
                    )
                else:
                    raw_tokens = self.token_counter.tool_message(
                        result_text,
                        call.name,
                        call.id,
                    ).tokens
                    guarded = GuardedToolResult(
                        content=result_text,
                        raw_result_tokens=raw_tokens,
                        context_result_tokens=raw_tokens,
                        is_truncated=False,
                    )
                if (
                    tool_governance_enabled
                    and guarded.is_truncated
                    and on_tool_artifact
                ):
                    await on_tool_artifact(
                        {
                            "source_ref": raw_source_ref,
                            "session_id": session_id,
                            "turn_id": turn_id,
                            "tool_name": call.name,
                            "content": result_text,
                        }
                    )
                tool_message = Message(
                    role="tool",
                    content=guarded.content,
                    name=call.name,
                    tool_call_id=call.id,
                )
                messages.append(tool_message)
                generated.append(tool_message)
                tool_result_tokens += guarded.context_result_tokens
                tool_calls += 1
                if on_tool_event:
                    await on_tool_event(
                        {
                            "type": "tool_completed",
                            "call_id": call.id,
                            "name": call.name,
                            "ok": tool_ok,
                            "error_code": tool_error_code,
                            "is_truncated": guarded.is_truncated,
                            "raw_result_tokens": guarded.raw_result_tokens,
                            "context_result_tokens": guarded.context_result_tokens,
                            "raw_source_ref": guarded.raw_source_ref,
                            "tool_governance_enabled": tool_governance_enabled,
                            "display": tool_display,
                            "retryable": tool_retryable,
                            "failure_type": tool_failure_type,
                            "metadata": tool_metadata,
                        }
                    )

        raise RuntimeContinuationRequired(
            generated=generated,
            usage=aggregate_usage(provider_usages),
            tool_calls=tool_calls,
            model_calls=model_calls,
        )


def aggregate_usage(usages: list[dict]) -> dict:
    if not usages:
        return {}
    if len(usages) == 1:
        return usages[0]

    def total(primary: str, fallback: str) -> int:
        result = 0
        for usage in usages:
            value = usage.get(primary, usage.get(fallback, 0))
            if isinstance(value, (int, float)):
                result += int(value)
        return result

    prompt = total("prompt_tokens", "input_tokens")
    completion = total("completion_tokens", "output_tokens")
    provider_total = total("total_tokens", "total_tokens")
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": provider_total or prompt + completion,
        "model_calls": len(usages),
        "provider_calls": usages,
    }
