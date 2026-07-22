from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from time import monotonic
from uuid import UUID
from pathlib import Path

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
from starter_agent.capabilities.gate import (
    PreToolCallGate,
    ToolExecutionDenied,
    UnifiedToolExecutor,
)
from starter_agent.capabilities.registry import UnifiedToolRegistry
from starter_agent.capabilities.store import CapabilityStore
from starter_agent.capabilities.models import PolicyRule
from starter_agent.capabilities.store import RecordAlreadyExistsError
from starter_agent.capabilities.confirmations import (
    ConfirmationService,
    TurnCoordinator,
)


class _BuiltinRegistryView:
    """Narrow adapter for test/embedded registries that expose a tool mapping."""

    def __init__(self, source):
        self._source = source
        self.email_manager = getattr(source, "email_manager", None)

    def list(self):
        return _builtin_tools(self._source)


def _builtin_tools(source):
    list_tools = getattr(source, "list", None)
    if callable(list_tools):
        return list(list_tools())
    mapping = getattr(source, "tools", None)
    if isinstance(mapping, dict):
        return list(mapping.values())
    raise TypeError("tool registry must expose list() or a tools mapping")


def _builtin_source(source):
    return source if callable(getattr(source, "list", None)) else _BuiltinRegistryView(source)


class AgentRuntime:
    def __init__(
        self,
        tools: ToolRegistry,
        policy: ToolPolicy,
        budget: RuntimeConfig,
        context_config: ContextConfig | None = None,
        *,
        gate: PreToolCallGate | None = None,
        executor: UnifiedToolExecutor | None = None,
        turn_coordinator: TurnCoordinator | None = None,
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
        if gate is None or executor is None:
            builtin_source = _builtin_source(tools)
            boundary_registry = (
                tools
                if isinstance(tools, UnifiedToolRegistry)
                else UnifiedToolRegistry(builtin_source)
            )
            capability_store = CapabilityStore("sqlite:///:memory:", Path("."))
            gate = PreToolCallGate(capability_store, registry=boundary_registry)
            executor = UnifiedToolExecutor(capability_store, gate=gate)
        self.gate = gate
        self.executor = executor
        self.turn_coordinator = turn_coordinator or TurnCoordinator(
            ConfirmationService(gate.store, gate)
        )
        safe_auto_tools = {
            "get_current_time",
            "search_jobs_serpapi",
            "search_job_description",
        }
        for builtin in _builtin_tools(tools):
            async def invoke(arguments, context, *, _tool=builtin):
                return await _tool.execute(dict(arguments), context)

            capability = self.gate.registry.resolve_execution(builtin.name)
            if capability is not None and builtin.name in safe_auto_tools:
                rule = PolicyRule(
                    id=f"builtin-auto-{builtin.name}",
                    server_id="builtin",
                    tool_name=builtin.name,
                    effect="allowlist_auto",
                    actions=("read",),
                    schema_hash=capability.schema_hash,
                    created_by="bootstrap",
                )
                try:
                    self.gate.store.create_policy_rule(rule)
                except RecordAlreadyExistsError:
                    pass
            network_guard = getattr(builtin, "network_guard_attestation", None)
            try:
                self.executor.register_invoker(
                    server_id="builtin",
                    tool_name=builtin.name,
                    invoker=invoke,
                    context_factory=self._context_for_request,
                    network_guard=network_guard,
                )
            except ToolExecutionDenied as exc:
                if exc.code != "network_guard_required":
                    raise

    async def execute_tool(
        self,
        *,
        tool_name: str,
        arguments: dict,
        session_id: UUID,
        turn_id: UUID,
        call_id: str,
        principal: str = "local-user",
        forced: bool = False,
        retry: bool = False,
        confirmation_id: str | None = None,
        on_tool_event: Callable[[dict], Awaitable[None]] | None = None,
    ):
        request = self.gate.request_for_tool(
            caller="model",
            principal=principal,
            session_id=str(session_id),
            turn_id=str(turn_id),
            call_id=call_id,
            tool_name=tool_name,
            arguments=arguments,
        )
        decision = (
            await self.gate.evaluate(request)
            if confirmation_id is None
            else await self.gate.evaluate_approved(
                request,
                confirmation_id=confirmation_id,
            )
        )
        if (
            confirmation_id is None
            and decision.outcome == "require_confirmation"
        ):
            decision = await self.turn_coordinator.wait_for_permit(
                request,
                decision,
                on_event=on_tool_event,
            )
        if decision.outcome != "allow":
            code = (
                "tool_confirmation_required"
                if decision.outcome == "require_confirmation"
                else decision.reason_code
            )
            raise ToolExecutionDenied(code)
        assert decision.permit is not None
        return await self.executor.execute(
            request,
            permit_id=decision.permit.id,
            forced=forced,
            retry=retry,
        )

    @staticmethod
    def _context_for_request(request):
        return ToolContext(
            session_id=UUID(request.session_id),
            turn_id=UUID(request.turn_id),
            tool_call_id=request.call_id,
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
        # Retain the compatibility parameter while making governance mandatory.
        tool_governance_enabled = True
        started = monotonic()
        model_calls = 0
        tool_calls = 0
        tool_result_tokens = 0
        repeated_calls: dict[str, int] = {}
        generated: list[Message] = []
        provider_usages: list[dict] = []
        context_revision = 0
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
            snapshot_reader = getattr(self.tools, "model_snapshot", None)
            if callable(snapshot_reader):
                snapshot = snapshot_reader()
                request_tools = snapshot.provider_tools()
                context_revision = snapshot.context_revision
            else:
                request_tools = self.tools.schemas()
                context_revision = 0
            logger.info(
                "model.requested",
                provider=provider.name,
                model=model,
                model_call=model_calls,
                context_revision=context_revision,
            )
            response = await provider.complete(
                messages,
                model,
                request_tools,
                on_delta=on_delta,
                tool_choice=(required_tool_name if model_calls == 1 else None),
            )
            response.context_revision = context_revision
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
                capability = self.gate.registry.resolve_execution(call.name)
                tool_ok = False
                tool_error_code: str | None = None
                tool_display = ""
                tool_retryable = False
                tool_failure_type: str | None = None
                tool_metadata: dict[str, object] = (
                    {
                        "server_id": capability.server_id,
                        "call_id": call.id,
                        "snapshot_id": capability.snapshot_id,
                        "schema_hash": capability.schema_hash,
                    }
                    if capability is not None
                    else {}
                )
                if tool is None and capability is None:
                    tool_error_code = "unknown_tool"
                    tool_display = "模型请求了未注册的工具"
                    result_text = json.dumps(
                        {"ok": False, "error_code": tool_error_code},
                        ensure_ascii=False,
                    )
                else:
                    try:
                        if tool is not None:
                            self.policy.check(tool)
                        logger.info(
                            "tool.requested",
                            tool=call.name,
                            risk_level=(
                                tool.risk_level
                                if tool is not None
                                else capability.risk_level
                            ),
                        )
                        result = await asyncio.wait_for(
                            self.execute_tool(
                                tool_name=call.name,
                                arguments=call.arguments,
                                session_id=session_id,
                                turn_id=turn_id,
                                call_id=call.id,
                                forced=required_tool_name == call.name,
                                retry=repeated_calls[signature] > 1,
                                on_tool_event=on_tool_event,
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
                            "server_id",
                            "call_id",
                            "snapshot_id",
                            "schema_hash",
                            "requested_url",
                            "final_url",
                            "source_url",
                            "source_content_sha256",
                        }
                        tool_metadata = {
                            key: value
                            for key, value in result.metadata.items()
                            if key in safe_metadata_keys
                        }
                        logger.info(
                            "tool.completed",
                            tool=call.name,
                            ok=result.ok,
                            error_code=result.error_code,
                            tool_governance_enabled=tool_governance_enabled,
                            retryable=result.retryable,
                            failure_type=tool_failure_type,
                        )
                    except (ToolPolicyError, ToolExecutionDenied) as exc:
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
                            tool=call.name,
                            error_type=type(exc).__name__,
                        )
                raw_source_ref = f"tool:{call.name}:{turn_id}:{call.id}"
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
                persisted_source_ref = None
                if on_tool_artifact:
                    await on_tool_artifact(
                        {
                            "source_ref": raw_source_ref,
                            "session_id": session_id,
                            "turn_id": turn_id,
                            "tool_name": call.name,
                            "call_id": call.id,
                            "content": guarded.redacted_content,
                            "server_id": tool_metadata.get("server_id"),
                            "snapshot_id": tool_metadata.get("snapshot_id"),
                            "schema_hash": tool_metadata.get("schema_hash"),
                            "requested_url": tool_metadata.get("requested_url"),
                            "final_url": tool_metadata.get("final_url"),
                            "source_url": tool_metadata.get(
                                "source_url", tool_metadata.get("final_url")
                            ),
                            "source_content_sha256": tool_metadata.get(
                                "source_content_sha256"
                            ),
                            "content_sha256": guarded.content_sha256,
                            "truncation_summary": {
                                "reason": guarded.truncation_reason,
                                "raw_bytes": guarded.raw_result_bytes,
                                "raw_chars": guarded.raw_result_chars,
                                "raw_tokens": guarded.raw_result_tokens,
                                "kept_bytes": guarded.kept_result_bytes,
                                "kept_chars": guarded.kept_result_chars,
                                "kept_tokens": guarded.kept_result_tokens,
                            },
                        }
                    )
                    persisted_source_ref = raw_source_ref
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
                            "raw_result_bytes": guarded.raw_result_bytes,
                            "raw_result_chars": guarded.raw_result_chars,
                            "kept_result_bytes": guarded.kept_result_bytes,
                            "kept_result_chars": guarded.kept_result_chars,
                            "kept_result_tokens": guarded.kept_result_tokens,
                            "content_sha256": tool_metadata.get(
                                "content_sha256", guarded.content_sha256
                            ),
                            "source_url": tool_metadata.get(
                                "source_url", tool_metadata.get("final_url")
                            ),
                            "server_id": tool_metadata.get("server_id"),
                            "requested_url": tool_metadata.get("requested_url"),
                            "final_url": tool_metadata.get("final_url"),
                            "source_content_sha256": tool_metadata.get(
                                "source_content_sha256"
                            ),
                            "snapshot_id": tool_metadata.get("snapshot_id"),
                            "schema_hash": tool_metadata.get("schema_hash"),
                            "truncation_reason": guarded.truncation_reason,
                            "raw_source_ref": (
                                persisted_source_ref or guarded.raw_source_ref
                            ),
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
            context_revision=context_revision,
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
