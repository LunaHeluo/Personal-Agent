from __future__ import annotations

import asyncio
import inspect
import json
from contextvars import ContextVar
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from time import monotonic
from uuid import UUID, uuid4
from pathlib import Path

from starter_agent.domain.errors import (
    RequiredToolNotCalledError,
    RuntimeBudgetExceeded,
    RuntimeContinuationRequired,
    ToolNotAvailableError,
    ToolsDisabledForTurnError,
    ToolPolicyError,
)
from starter_agent.domain.models import Message, ModelResponse
from starter_agent.agent.token_counter import TokenCounter
from starter_agent.agent.tool_result_guard import (
    GuardedToolResult,
    ToolResultGuard,
    redact_tool_result_content,
    sanitize_provenance_url,
)
from starter_agent.observability.logging import get_logger
from starter_agent.providers.base import Provider
from starter_agent.runtime_revision import RuntimeRevision
from starter_agent.settings import ContextConfig, RuntimeConfig
from starter_agent.tools.base import ToolContext
from starter_agent.tools.policy import ToolPolicy
from starter_agent.tools.registry import ToolRegistry
from starter_agent.capabilities.gate import (
    PreToolCallGate,
    ToolExecutionDenied,
    UnifiedToolExecutor,
)
from starter_agent.capabilities.registry import ModelToolSnapshot, UnifiedToolRegistry
from starter_agent.capabilities.store import CapabilityStore
from starter_agent.capabilities.models import (
    AuditEvent,
    PolicyRule,
    canonical_json_sha256,
)
from starter_agent.capabilities.store import RecordAlreadyExistsError
from starter_agent.capabilities.confirmations import (
    ConfirmationService,
    TurnCoordinator,
)
from starter_agent.delegation.context import (
    RunBudgetExceeded,
    RunContext,
    RunTraceContext,
    RunToolViewStale,
)
from starter_agent.delegation.models import BudgetLimits, RunOutcome, RunSpec


_ACTIVE_RUN_CONTEXT: ContextVar[RunContext | None] = ContextVar(
    "active_run_context", default=None
)
_ACTIVE_RUN_ROLE: ContextVar[str] = ContextVar("active_run_role", default="user")


def _structured_job_from_snapshot_result(
    data: Mapping[str, object],
    metadata: Mapping[str, object],
    artifact_ref: str | None,
) -> dict[str, object] | None:
    """Project the MCP wrapper to the normalized JD contract used by Child state."""

    nested = data.get("structured_content")
    candidate = nested if isinstance(nested, Mapping) else data
    if not isinstance(candidate, Mapping) or not candidate:
        return None
    allowed_fields = {
        "title",
        "company",
        "location",
        "responsibilities",
        "requirements",
        "source_url",
        "final_url",
        "retrieved_at",
        "validation_state",
        "content_hash",
        "artifact_refs",
    }
    projected = {
        key: value for key, value in candidate.items() if key in allowed_fields
    }
    source_url = projected.get("source_url") or metadata.get("source_url")
    final_url = projected.get("final_url") or metadata.get("final_url") or source_url
    content_hash = (
        projected.get("content_hash")
        or metadata.get("source_content_sha256")
        or metadata.get("content_sha256")
    )
    refs = projected.get("artifact_refs")
    if not isinstance(refs, (list, tuple)):
        refs = [artifact_ref] if artifact_ref else []
    projected.update(
        source_url=source_url,
        final_url=final_url,
        content_hash=content_hash,
        artifact_refs=list(refs),
    )
    projected.setdefault("retrieved_at", datetime.now(UTC).isoformat())
    return projected


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


def _reconcile_bootstrap_auto_rule(store, capability) -> None:
    """Keep only the app-owned, read-only builtin rule bound to its schema."""

    if not (
        capability.server_id == "builtin"
        and capability.enabled
        and capability.connected
        and capability.review_state == "approved"
        and capability.risk_level == "read"
    ):
        return
    rule_id = f"builtin-auto-{capability.canonical_name}"
    existing = store.get_policy_rule(rule_id)
    if existing is None:
        store.create_policy_rule(
            PolicyRule(
                id=rule_id,
                server_id="builtin",
                tool_name=capability.canonical_name,
                effect="allowlist_auto",
                actions=("read",),
                schema_hash=capability.schema_hash,
                created_by="bootstrap",
            )
        )
        return
    safe_scope = (
        existing.id == rule_id
        and existing.server_id == "builtin"
        and existing.tool_name == capability.canonical_name
        and existing.effect == "allowlist_auto"
        and existing.actions == ("read",)
        and existing.created_by == "bootstrap"
        and existing.enabled
        and not existing.schemes
        and not existing.domains
        and not existing.parameter_constraints
        and not existing.data_classes
        and not existing.roles
        and existing.expires_at is None
    )
    if safe_scope and existing.schema_hash != capability.schema_hash:
        store.update_policy_rule(
            existing.id,
            expected_revision=existing.revision,
            schema_hash=capability.schema_hash,
        )


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
        knowledge_scope=None,
        knowledge_base_id: UUID | None = None,
        runtime_revision: RuntimeRevision | None = None,
        provider_resolver: Callable[[str], Provider | None] | None = None,
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
        self.knowledge_scope = knowledge_scope
        self.knowledge_base_id = knowledge_base_id
        self.runtime_revision = runtime_revision
        self.provider_resolver = provider_resolver
        safe_auto_tools = {
            "get_current_time",
            "search_jobs_serpapi",
            "retrieve_resume_evidence",
        }
        for builtin in _builtin_tools(tools):
            async def invoke(arguments, context, *, _tool=builtin):
                return await _tool.execute(dict(arguments), context)

            capability = self.gate.registry.resolve_execution(builtin.name)
            if capability is not None and builtin.name in safe_auto_tools:
                try:
                    _reconcile_bootstrap_auto_rule(
                        self.gate.store,
                        capability,
                    )
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
        active_context = _ACTIVE_RUN_CONTEXT.get()
        if active_context is not None and active_context.refresh_cancellation():
            raise RuntimeBudgetExceeded("Run cancelled")
        request = self.gate.request_for_tool(
            caller="model",
            principal=principal,
            session_id=str(session_id),
            turn_id=str(turn_id),
            call_id=call_id,
            tool_name=tool_name,
            arguments=arguments,
            role=_ACTIVE_RUN_ROLE.get(),
        )
        decision = (
            await self.gate.evaluate(request)
            if confirmation_id is None
            else await self.gate.evaluate_approved(
                request,
                confirmation_id=confirmation_id,
            )
        )
        gate_audit_ref = self._append_audit(
            action="gate.evaluated",
            target=f"tool:{request.server_id}:{request.tool_name}",
            decision=decision.outcome,
            reason_code=decision.reason_code,
            session_id=str(session_id),
            turn_id=str(turn_id),
            call_id=call_id,
            payload={
                "request_hash": request.request_hash,
                "schema_hash": request.schema_hash,
                "snapshot_id": request.snapshot_id,
            },
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
        trace_ref = f"trace:{session_id}:{turn_id}:{call_id}"
        started_audit_ref = self._append_audit(
            action="tool.started",
            target=f"tool:{request.server_id}:{request.tool_name}",
            decision="allow",
            reason_code="gate_revalidated",
            session_id=str(session_id),
            turn_id=str(turn_id),
            call_id=call_id,
            payload={
                "confirmation_id": decision.permit.confirmation_id,
                "gate_audit_ref": gate_audit_ref,
                "request_hash": request.request_hash,
            },
        )
        if on_tool_event is not None:
            await on_tool_event(
                {
                    "type": "tool_started",
                    "name": tool_name,
                    "call_id": call_id,
                    "confirmation_id": decision.permit.confirmation_id,
                    "session_id": str(session_id),
                    "turn_id": str(turn_id),
                    "server_id": request.server_id,
                    "audit_ref": started_audit_ref,
                    "trace_ref": trace_ref,
                }
            )
        return await self.executor.execute(
            request,
            permit_id=decision.permit.id,
            forced=forced,
            retry=retry,
        )

    async def replay_persisted_delegate_call(
        self,
        *,
        spec: RunSpec,
        context: RunContext,
        call,
        on_tool_event: Callable[[dict], Awaitable[None]] | None = None,
    ):
        if spec.role != "coordinator" or context.child_task_id is not None or call.name != "delegate_task":
            raise ToolExecutionDenied("delegate_batch_replay_forbidden")
        if call.name not in set(spec.allowed_tools) & set(context.effective_tool_view):
            raise ToolExecutionDenied("delegate_batch_replay_forbidden")
        if context.refresh_cancellation():
            raise RuntimeBudgetExceeded("Run cancelled")
        if context.budget.consumed.tool_calls >= context.budget.limits.tool_calls:
            raise RuntimeBudgetExceeded("Maximum tool calls exceeded")
        token = _ACTIVE_RUN_CONTEXT.set(context)
        role_token = _ACTIVE_RUN_ROLE.set("coordinator")
        try:
            result = await self.execute_tool(
                tool_name=call.name,
                arguments=call.arguments,
                session_id=context.session_id,
                turn_id=context.turn_id,
                call_id=call.id,
                principal=context.principal,
                retry=True,
                on_tool_event=on_tool_event,
            )
            context.budget.consume(tool_calls=1)
            return result
        finally:
            _ACTIVE_RUN_ROLE.reset(role_token)
            _ACTIVE_RUN_CONTEXT.reset(token)

    def _append_audit(
        self,
        *,
        action: str,
        target: str,
        decision: str,
        reason_code: str,
        session_id: str,
        turn_id: str,
        call_id: str,
        payload: dict | None = None,
    ) -> str:
        active = _ACTIVE_RUN_CONTEXT.get()
        controlled_payload = dict(payload or {})
        if active is not None and active.child_task_id is not None:
            controlled_payload.update(
                {
                    "parent_run_id": active.parent_run_id,
                    "child_task_id": active.child_task_id,
                    "child_run_id": active.trace_context.child_run_id,
                    "eval_run_id": active.trace_context.eval_run_id,
                    "case_id": active.trace_context.case_id,
                    "model_request_id": active.trace_context.model_request_id,
                    "principal": active.principal,
                    "access_level": "child_restricted",
                    "policy_decision_id": (
                        controlled_payload.get("policy_decision_id")
                        or active.trace_context.policy_decision_id
                    ),
                    "approval_id": (
                        controlled_payload.get("approval_id")
                        or active.trace_context.approval_id
                    ),
                }
            )
        event = AuditEvent(
            event_id=f"audit-{uuid4().hex}",
            actor="runtime",
            action=action,
            target=target,
            decision=decision,
            reason_code=reason_code,
            session_id=session_id,
            turn_id=turn_id,
            call_id=call_id,
            payload=controlled_payload,
            created_at=datetime.now(UTC),
        )
        self.gate.store.append_audit_event(event)
        return event.event_id

    def _tool_was_invoked(self, call_id: str) -> bool:
        return any(
            event.action == "tool.invoked" and event.call_id == call_id
            for event in self.gate.store.list_audit_events()
        )

    def _context_for_request(self, request):
        active = _ACTIVE_RUN_CONTEXT.get()
        if active is not None:
            return active.tool_context(request.call_id, run_role=_ACTIVE_RUN_ROLE.get())
        return ToolContext(
            session_id=UUID(request.session_id),
            turn_id=UUID(request.turn_id),
            tool_call_id=request.call_id,
            user_id=(
                None
                if self.knowledge_scope is None
                else self.knowledge_scope.user_id
            ),
            project_id=(
                None
                if self.knowledge_scope is None
                else self.knowledge_scope.project_id
            ),
            knowledge_base_id=self.knowledge_base_id,
        )

    async def run(self, *args, **kwargs):
        if "spec" in kwargs or "context" in kwargs:
            try:
                spec = kwargs.pop("spec")
                context = kwargs.pop("context")
            except KeyError as exc:
                raise TypeError("run(spec, context) requires both arguments") from exc
            if args or not isinstance(spec, RunSpec) or not isinstance(context, RunContext):
                raise TypeError("run(spec, context) requires RunSpec and RunContext")
            return await self._run_spec(spec, context, **kwargs)
        if args and isinstance(args[0], RunSpec):
            if len(args) < 2 or not isinstance(args[1], RunContext):
                raise TypeError("run(spec, context) requires a RunContext")
            return await self._run_spec(args[0], args[1], **kwargs)
        return await self._run_legacy(*args, **kwargs)

    async def _run_legacy(
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
        allow_tools: bool = True,
        run_role: str = "user",
    ) -> tuple[ModelResponse, list[Message], int]:
        limits = BudgetLimits(
            tokens=self.context_config.max_total_tokens,
            cost_microunits=2**63 - 1,
            wall_clock_ms=max(1, int(self.budget.max_seconds * 1000)),
            model_calls=self.budget.max_model_calls,
            tool_calls=self.budget.max_tool_calls,
        )
        scope = self.knowledge_scope
        context = RunContext(
            run_id=f"chat:{turn_id}",
            parent_run_id=f"chat:{turn_id}",
            session_id=session_id,
            turn_id=turn_id,
            principal="local-user",
            messages=messages,
            effective_tool_view=[],
            budget_limits=limits,
            trace_context=RunTraceContext(parent_run_id=f"chat:{turn_id}"),
            user_id=None if scope is None else scope.user_id,
            project_id=None if scope is None else scope.project_id,
            knowledge_base_id=self.knowledge_base_id,
        )
        response, generated, calls = await self._run_loop(
            provider=provider,
            model=model,
            context=context,
            runtime_budget=self.budget,
            on_delta=on_delta,
            required_tool_name=required_tool_name,
            on_tool_event=on_tool_event,
            on_tool_artifact=on_tool_artifact,
            tool_governance_enabled=tool_governance_enabled,
            allow_tools=allow_tools,
            run_role="user",
        )
        messages.extend(context.messages[len(messages) :])
        return response, generated, calls

    async def _run_spec(
        self,
        spec: RunSpec,
        context: RunContext,
        *,
        on_delta: Callable[[str], Awaitable[None]] | None = None,
        on_tool_event: Callable[[dict], Awaitable[None]] | None = None,
        on_tool_artifact: Callable[[dict], Awaitable[None]] | None = None,
    ) -> RunOutcome:
        if spec.run_id != context.run_id:
            raise ValueError("RunSpec and RunContext run IDs must match")
        context_kind = "child" if context.child_task_id is not None else "parent"
        if spec.run_kind != context_kind:
            raise ValueError("RunSpec run kind does not match RunContext identity")
        if self.provider_resolver is None:
            raise RuntimeError("provider_resolver_unavailable")
        provider = self.provider_resolver(spec.provider)
        if provider is None:
            raise RuntimeError(f"provider_not_found:{spec.provider}")
        if context.refresh_cancellation():
            return RunOutcome(
                disposition="cancelled",
                run_id=spec.run_id,
                status="cancelled",
                error_code="run_cancelled",
            )
        if context.child_task_id is not None:
            try:
                self._validate_pinned_tool_view(context)
            except RunToolViewStale:
                return RunOutcome(
                    disposition="failed",
                    run_id=spec.run_id,
                    status="failed",
                    error_code="runtime_tool_view_stale",
                )
        if (
            context.budget.consumed.model_calls >= context.budget.limits.model_calls
            or context.budget.consumed.wall_clock_ms
            >= context.budget.limits.wall_clock_ms
        ):
            return RunOutcome(
                disposition="failed",
                run_id=spec.run_id,
                status="budget_exhausted",
                error_code="runtime_budget_exceeded",
            )
        runtime_budget = RuntimeConfig(
            max_model_calls=min(
                context.budget.consumed.model_calls + spec.max_steps,
                context.budget.limits.model_calls,
            ),
            max_tool_calls=context.budget.limits.tool_calls,
            max_seconds=max(
                0.001,
                (
                    context.budget.limits.wall_clock_ms
                    - context.budget.consumed.wall_clock_ms
                )
                / 1000,
            ),
            tool_timeout_seconds=min(
                self.budget.tool_timeout_seconds,
                (
                    context.per_tool_timeout_seconds
                    if context.per_tool_timeout_seconds is not None
                    else self.budget.tool_timeout_seconds
                ),
                max(
                    0.001,
                    (
                        context.budget.limits.wall_clock_ms
                        - context.budget.consumed.wall_clock_ms
                    )
                    / 1000,
                ),
            ),
        )
        context.effective_tool_view = sorted(
            set(context.effective_tool_view) & set(spec.allowed_tools)
        )
        context.effective_tool_view_enforced = True
        token = _ACTIVE_RUN_CONTEXT.set(context)
        role_token = _ACTIVE_RUN_ROLE.set(spec.role)
        attempt_started = monotonic()
        try:
            try:
                response, _generated, _tool_calls = await self._run_loop(
                    provider=provider,
                    model=spec.model,
                    context=context,
                    runtime_budget=runtime_budget,
                    on_delta=on_delta,
                    on_tool_event=on_tool_event,
                    on_tool_artifact=on_tool_artifact,
                    allow_tools=bool(spec.allowed_tools),
                    run_role=spec.role,
                )
                if context.suspension_requested:
                    self._settle_run_wall_clock(context, attempt_started)
                    return RunOutcome(
                        disposition="suspended",
                        run_id=spec.run_id,
                        status="waiting_children",
                        checkpoint_ref=context.suspension_checkpoint_ref,
                    )
            except RunToolViewStale:
                self._settle_run_wall_clock(context, attempt_started)
                return RunOutcome(
                    disposition="failed",
                    run_id=spec.run_id,
                    status="failed",
                    error_code="runtime_tool_view_stale",
                )
            except TimeoutError:
                self._settle_run_wall_clock(context, attempt_started)
                return RunOutcome(
                    disposition="failed",
                    run_id=spec.run_id,
                    status="timed_out",
                    error_code="runtime_timeout",
                )
            except (RuntimeBudgetExceeded, RunBudgetExceeded):
                self._settle_run_wall_clock(context, attempt_started)
                return RunOutcome(
                    disposition=(
                        "cancelled" if context.cancellation.requested else "failed"
                    ),
                    run_id=spec.run_id,
                    status=(
                        "cancelled"
                        if context.cancellation.requested
                        else "budget_exhausted"
                    ),
                    error_code=(
                        "run_cancelled"
                        if context.cancellation.requested
                        else "runtime_budget_exceeded"
                    ),
                )
            except BaseException:
                self._settle_run_wall_clock(context, attempt_started)
                raise
        finally:
            _ACTIVE_RUN_ROLE.reset(role_token)
            _ACTIVE_RUN_CONTEXT.reset(token)
        if self._settle_run_wall_clock(context, attempt_started):
            return RunOutcome(
                disposition="failed",
                run_id=spec.run_id,
                status="budget_exhausted",
                error_code="runtime_budget_exceeded",
            )
        context.output_buffer.append(response.content or "")
        return RunOutcome(
            disposition="completed",
            run_id=spec.run_id,
            status="succeeded",
            output_ref=f"context-output:{spec.run_id}:{context.context_version}",
        )

    @staticmethod
    def _settle_run_wall_clock(context: RunContext, started: float) -> bool:
        elapsed_ms = max(1, int((monotonic() - started) * 1000))
        try:
            context.budget.consume(wall_clock_ms=elapsed_ms)
        except RunBudgetExceeded:
            return True
        return False

    async def _run_loop(
        self,
        *,
        provider: Provider,
        model: str,
        context: RunContext,
        runtime_budget: RuntimeConfig,
        on_delta: Callable[[str], Awaitable[None]] | None = None,
        required_tool_name: str | None = None,
        on_tool_event: Callable[[dict], Awaitable[None]] | None = None,
        on_tool_artifact: Callable[[dict], Awaitable[None]] | None = None,
        tool_governance_enabled: bool = True,
        allow_tools: bool = True,
        run_role: str = "user",
    ) -> tuple[ModelResponse, list[Message], int]:
        messages = context.messages
        session_id = context.session_id
        turn_id = context.turn_id
        # Retain the compatibility parameter while making governance mandatory.
        tool_governance_enabled = True
        started = monotonic()
        model_calls = context.budget.consumed.model_calls
        tool_calls = context.budget.consumed.tool_calls
        tool_result_tokens = context.tool_result_tokens
        repeated_calls = context.repeated_calls
        generated: list[Message] = []
        provider_usages = context.provider_usages
        context_revision = 0
        logger = get_logger(session_id=str(session_id), turn_id=str(turn_id))
        if required_tool_name and not allow_tools:
            raise ToolsDisabledForTurnError()
        if required_tool_name:
            required_tool = self.tools.get(required_tool_name)
            if required_tool is None:
                raise ToolNotAvailableError()
            self.policy.check(required_tool)

        while model_calls < runtime_budget.max_model_calls:
            if context.deadline_at is not None and datetime.now(UTC) >= context.deadline_at:
                raise TimeoutError("run deadline exhausted")
            if context.refresh_cancellation():
                raise RuntimeBudgetExceeded("Run cancelled")
            if monotonic() - started > runtime_budget.max_seconds:
                raise RuntimeBudgetExceeded("Maximum run time exceeded")
            self._validate_pinned_tool_view(context)
            model_calls += 1
            context.budget.consume(model_calls=1)
            snapshot_reader = getattr(self.tools, "model_snapshot", None)
            if not allow_tools:
                request_tools = []
                context_revision = (
                    getattr(self.tools, "context_revision", 0) or 0
                )
            elif callable(snapshot_reader):
                snapshot = (
                    context.tool_schema_snapshot
                    or (
                        context.tool_view.model_snapshot()
                        if context.tool_view is not None
                        else snapshot_reader()
                    )
                )
                request_tools = snapshot.provider_tools()
                context_revision = snapshot.context_revision
            else:
                request_tools = self.tools.schemas()
                context_revision = 0
            effective_names = set(context.effective_tool_view)
            if context.effective_tool_view_enforced:
                request_tools = [
                    definition
                    for definition in request_tools
                    if definition.get("function", {}).get("name")
                    in effective_names
                ]
            callable_tools = []
            for definition in request_tools:
                function = definition.get("function", {})
                name = function.get("name")
                parameters = function.get("parameters")
                if isinstance(name, str) and isinstance(parameters, dict):
                    callable_tools.append(
                        {
                            "name": name,
                            "schema_hash": canonical_json_sha256(parameters),
                        }
                    )
            self._append_audit(
                action="model.context.snapshot",
                target=f"provider:{provider.name}:{model}",
                decision="allow",
                reason_code="provider_request_prepared",
                session_id=str(session_id),
                turn_id=str(turn_id),
                call_id=f"model-call-{model_calls}",
                payload={
                    "model_call": model_calls,
                    "context_revision": context_revision,
                    "provider_tools_hash": canonical_json_sha256(request_tools),
                    "callable_tools": callable_tools,
                    **(
                        {"runtime_revision": self.runtime_revision.id}
                        if self.runtime_revision is not None
                        else {}
                    ),
                },
            )
            logger.info(
                "model.requested",
                provider=provider.name,
                model=model,
                model_call=model_calls,
                context_revision=context_revision,
            )
            complete_parameters = inspect.signature(provider.complete).parameters
            complete_kwargs = {
                "on_delta": on_delta,
                "tool_choice": (
                    required_tool_name if model_calls == 1 else None
                ),
            }
            remaining_tokens = (
                context.budget.limits.tokens - context.budget.consumed.tokens
            )
            estimated_input = self.token_counter.messages(
                messages, request_tools
            ).tokens
            if estimated_input >= remaining_tokens:
                raise RunBudgetExceeded("insufficient token budget for model request")
            if (
                "max_output_tokens" in complete_parameters
                or any(
                    parameter.kind is inspect.Parameter.VAR_KEYWORD
                    for parameter in complete_parameters.values()
                )
            ):
                complete_kwargs["max_output_tokens"] = (
                    remaining_tokens - estimated_input
                )
            if (
                "context_revision" in complete_parameters
                or any(
                    parameter.kind is inspect.Parameter.VAR_KEYWORD
                    for parameter in complete_parameters.values()
                )
            ):
                complete_kwargs["context_revision"] = context_revision
            remaining_seconds = runtime_budget.max_seconds - (monotonic() - started)
            if context.deadline_at is not None:
                remaining_seconds = min(
                    remaining_seconds,
                    (context.deadline_at - datetime.now(UTC)).total_seconds(),
                )
            if remaining_seconds <= 0:
                raise TimeoutError("run wall-clock budget exhausted")
            response = await asyncio.wait_for(
                provider.complete(
                    messages,
                    model,
                    request_tools,
                    **complete_kwargs,
                ),
                timeout=remaining_seconds,
            )
            if context.refresh_cancellation():
                raise RuntimeBudgetExceeded("Run cancelled")
            response.context_revision = context_revision
            if response.usage:
                provider_usages.append(response.usage)
                total_tokens = response.usage.get(
                    "total_tokens",
                    response.usage.get("input_tokens", 0)
                    + response.usage.get("output_tokens", 0),
                )
                increments: dict[str, int] = {}
                if isinstance(total_tokens, (int, float)):
                    increments["tokens"] = max(0, int(total_tokens))
                cost = response.usage.get("cost_microunits")
                if isinstance(cost, (int, float)):
                    increments["cost_microunits"] = max(0, int(cost))
                if increments:
                    context.budget.consume(**increments)
                if (
                    not isinstance(cost, (int, float))
                    and context.budget.limits.cost_microunits < 2**63 - 1
                ):
                    context.budget.mark_cost_unknown()
                    raise RunBudgetExceeded("provider cost usage is unavailable")
            logger.info(
                "model.completed",
                provider=response.provider,
                model=response.model,
                tool_call_count=len(response.tool_calls),
                usage=response.usage,
            )
            if not allow_tools and response.tool_calls:
                raise ToolsDisabledForTurnError()
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
            if (
                context.max_tool_calls_per_response is not None
                and len(response.tool_calls) > context.max_tool_calls_per_response
            ):
                context.boundary_stop_reason = "tool_batch_limit"
                return ModelResponse(content="", provider=provider.name, model=model, usage=aggregate_usage(provider_usages)), generated, tool_calls

            if context.tool_preflight_probe is not None:
                for call in response.tool_calls:
                    preflight = context.tool_preflight_probe(call, context)
                    if preflight is not None:
                        action, reason = preflight[:2]
                        if action not in {"skip", "stop"}:
                            raise ValueError("invalid_tool_preflight_action")
                        if action == "stop":
                            context.boundary_stop_reason = reason
                        # The rejected call never reaches Gate/Tool execution.
                        # Record a controlled Observation so the transcript is
                        # structurally complete without inventing a Tool result.
                        observation: dict[str, object] = {
                            "ok": False,
                            "error_code": reason,
                        }
                        if len(preflight) == 3:
                            details = preflight[2]
                            if not isinstance(details, dict):
                                raise ValueError("invalid_tool_preflight_details")
                            observation.update(details)
                        stopped = Message(
                            role="tool",
                            content=json.dumps(observation, ensure_ascii=False),
                            name=call.name,
                            tool_call_id=call.id,
                        )
                        messages.append(
                            Message(
                                role="assistant",
                                content=response.content or "",
                                tool_calls=response.tool_calls,
                            )
                        )
                        messages.append(stopped)
                        generated.extend((messages[-2], stopped))
                        if action == "stop":
                            return (
                                ModelResponse(
                                    content="", provider=provider.name, model=model,
                                    usage=aggregate_usage(provider_usages),
                                ), generated, tool_calls,
                            )
                        break
                else:
                    preflight = None
                if preflight is not None and preflight[0] == "skip":
                    continue
            assistant_tool_message = Message(
                role="assistant",
                content=response.content or "",
                tool_calls=response.tool_calls,
            )
            messages.append(assistant_tool_message)
            generated.append(assistant_tool_message)
            delegate_calls = tuple(call for call in response.tool_calls if call.name == "delegate_task")
            if delegate_calls and len(delegate_calls) != len(response.tool_calls):
                raise ValueError("mixed_delegate_batch_forbidden")
            if delegate_calls and context.delegate_batch_probe is not None:
                context.delegate_batch_probe(delegate_calls)
            for call in response.tool_calls:
                if context.deadline_at is not None and datetime.now(UTC) >= context.deadline_at:
                    raise TimeoutError("run deadline exhausted")
                self._validate_pinned_tool_view(context)
                self._append_audit(
                    action="tool.requested",
                    target=f"tool:{call.name}",
                    decision="allow",
                    reason_code="model_requested_tool",
                    session_id=str(session_id),
                    turn_id=str(turn_id),
                    call_id=call.id,
                    payload={"tool_name": call.name},
                )
                if tool_calls >= runtime_budget.max_tool_calls:
                    raise RuntimeBudgetExceeded("Maximum tool calls exceeded")
                repeat_scope = (
                    context.repeated_call_scope_probe(call, context)
                    if context.repeated_call_scope_probe is not None
                    else "run"
                )
                signature = f"{repeat_scope}:{call.name}:{json.dumps(call.arguments, sort_keys=True)}"
                repeated_calls[signature] = repeated_calls.get(signature, 0) + 1
                if repeated_calls[signature] > 2:
                    raise RuntimeBudgetExceeded(
                        f"Repeated identical tool call detected: {call.name}"
                    )
                tool = self.tools.get(call.name)
                capability = self.gate.registry.resolve_execution(call.name)
                if (
                    context.effective_tool_view_enforced
                    and call.name not in context.effective_tool_view
                ):
                    tool = None
                    capability = None
                trusted_provenance = (
                    {
                        "server_id": capability.server_id,
                        "call_id": call.id,
                        "snapshot_id": capability.snapshot_id,
                        "schema_hash": capability.schema_hash,
                    }
                    if capability is not None
                    else {"call_id": call.id}
                )
                tool_ok = False
                tool_error_code: str | None = None
                tool_display = ""
                tool_retryable = False
                tool_failure_type: str | None = None
                tool_metadata: dict[str, object] = dict(trusted_provenance)
                if call.name == "mcp__playwright__browser_navigate":
                    requested_url = sanitize_provenance_url(
                        call.arguments.get("url")
                    )
                    if requested_url is not None:
                        tool_metadata["requested_url"] = requested_url
                execution_event_context: dict[str, object] = {}
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
                        async def forward_tool_event(event: dict) -> None:
                            if event.get("call_id") == call.id:
                                for key in (
                                    "confirmation_id",
                                    "session_id",
                                    "turn_id",
                                    "server_id",
                                    "audit_ref",
                                    "trace_ref",
                                ):
                                    value = event.get(key)
                                    if value is not None:
                                        execution_event_context[key] = value
                            if on_tool_event is not None:
                                await on_tool_event(event)

                        tool_timeout = runtime_budget.tool_timeout_seconds
                        if context.deadline_at is not None:
                            tool_timeout = min(
                                tool_timeout,
                                (context.deadline_at - datetime.now(UTC)).total_seconds(),
                            )
                        if tool_timeout <= 0:
                            raise TimeoutError("run deadline exhausted")
                        result = await asyncio.wait_for(
                            self.execute_tool(
                                tool_name=call.name,
                                arguments=call.arguments,
                                session_id=session_id,
                                turn_id=turn_id,
                                call_id=call.id,
                                principal=context.principal,
                                forced=required_tool_name == call.name,
                                retry=repeated_calls[signature] > 1,
                                on_tool_event=(
                                    forward_tool_event
                                    if on_tool_event is not None
                                    else None
                                ),
                            ),
                            timeout=tool_timeout,
                        )
                        if context.refresh_cancellation():
                            raise RuntimeBudgetExceeded("Run cancelled")
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
                            "requested_url",
                            "final_url",
                            "source_url",
                            "source_content_sha256",
                            "artifact_ref",
                            "status_code",
                        }
                        tool_metadata = {
                            key: value
                            for key, value in result.metadata.items()
                            if key in safe_metadata_keys
                        }
                        status_code = tool_metadata.get("status_code")
                        if not isinstance(status_code, int) or not 100 <= status_code <= 599:
                            tool_metadata.pop("status_code", None)
                        tool_metadata.update(trusted_provenance)
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
                    except (RuntimeBudgetExceeded, RunBudgetExceeded):
                        raise
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
                artifact_guarded = guarded
                if (
                    run_role == "specialist"
                    and call.name == "mcp__playwright__browser_snapshot"
                ):
                    # Browser HTML is a restricted child artifact, not a model
                    # observation.  Reuse the shared result guard for both
                    # persistence redaction and the bounded JD observation.
                    from starter_agent.delegation.web_context import WebContextGovernor

                    seen = context.working_memory.setdefault(
                        "web_context_seen_dom_hashes", set()
                    )
                    if not isinstance(seen, set):
                        seen = set(seen) if isinstance(seen, list) else set()
                        context.working_memory["web_context_seen_dom_hashes"] = seen
                    governed = WebContextGovernor(
                        self.token_counter, guard.max_result_tokens
                    ).govern(
                        result_text,
                        tool_name=call.name,
                        tool_call_id=call.id,
                        raw_source_ref=raw_source_ref,
                        seen_dom_hashes=seen,
                    )
                    artifact_guarded = governed.artifact
                    guarded = governed.observation
                    context.artifact_refs.append(raw_source_ref)
                for url_field in ("requested_url", "final_url", "source_url"):
                    sanitized_url = sanitize_provenance_url(
                        tool_metadata.get(url_field)
                    )
                    if sanitized_url is None:
                        tool_metadata.pop(url_field, None)
                    else:
                        tool_metadata[url_field] = sanitized_url
                persisted_source_ref = None
                if on_tool_artifact:
                    await on_tool_artifact(
                        {
                            "source_ref": raw_source_ref,
                            "session_id": session_id,
                            "turn_id": turn_id,
                            "tool_name": call.name,
                            "call_id": call.id,
                            "content": artifact_guarded.redacted_content,
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
                            "artifact_ref": tool_metadata.get("artifact_ref"),
                            "content_sha256": guarded.content_sha256,
                            # The Worker persists these identifiers as restricted
                            # RAG evidence links; the evidence body never enters a
                            # Parent context or public trace.
                            "evidence_refs": (
                                [
                                    {
                                        "chunk_id": item.get("chunk_id"),
                                        "source_ref": item.get("source_ref"),
                                        "document_id": item.get("document_id"),
                                    }
                                    for item in result.data.get("evidence", [])
                                    if isinstance(item, dict)
                                    and isinstance(item.get("chunk_id"), str)
                                    and isinstance(item.get("source_ref"), str)
                                ]
                                if call.name == "retrieve_resume_evidence"
                                and tool_ok and isinstance(result.data, dict)
                                else []
                            ),
                            "truncation_summary": {
                                "reason": artifact_guarded.truncation_reason,
                                "raw_bytes": artifact_guarded.raw_result_bytes,
                                "raw_chars": artifact_guarded.raw_result_chars,
                                "raw_tokens": artifact_guarded.raw_result_tokens,
                                "kept_bytes": artifact_guarded.kept_result_bytes,
                                "kept_chars": artifact_guarded.kept_result_chars,
                                "kept_tokens": artifact_guarded.kept_result_tokens,
                            },
                            "parent_run_id": context.parent_run_id,
                            "child_task_id": context.child_task_id,
                            "child_run_id": context.trace_context.child_run_id,
                            "policy_decision_id": context.trace_context.policy_decision_id,
                            "approval_id": execution_event_context.get("confirmation_id") or context.trace_context.approval_id,
                            "access_level": "child_restricted",
                            "principal": context.principal,
                        }
                    )
                    persisted_source_ref = raw_source_ref
                def safe_optional_text(value: object) -> str | None:
                    if not isinstance(value, str) or not value:
                        return None
                    return redact_tool_result_content(value)

                completed_payload = {
                    "tool_name": call.name,
                    "ok": tool_ok,
                    "error_code": safe_optional_text(tool_error_code),
                    "server_id": tool_metadata.get("server_id"),
                    "call_id": call.id,
                    "snapshot_id": tool_metadata.get("snapshot_id"),
                    "schema_hash": tool_metadata.get("schema_hash"),
                    "raw_source_ref": (
                        persisted_source_ref or guarded.raw_source_ref
                    ),
                    "requested_url": tool_metadata.get("requested_url"),
                    "final_url": tool_metadata.get("final_url"),
                    "source_url": tool_metadata.get(
                        "source_url", tool_metadata.get("final_url")
                    ),
                    "content_sha256": guarded.content_sha256,
                    "is_truncated": bool(guarded.is_truncated),
                    "truncation_reason": safe_optional_text(
                        guarded.truncation_reason
                    ),
                    "raw_result_bytes": int(guarded.raw_result_bytes),
                    "raw_result_chars": int(guarded.raw_result_chars),
                    "raw_result_tokens": int(guarded.raw_result_tokens),
                    "kept_result_bytes": int(guarded.kept_result_bytes),
                    "kept_result_chars": int(guarded.kept_result_chars),
                    "kept_result_tokens": int(guarded.kept_result_tokens),
                    "context_result_tokens": int(
                        guarded.context_result_tokens
                    ),
                    "confirmation_id": execution_event_context.get(
                        "confirmation_id"
                    ),
                    "tool_invoked": self._tool_was_invoked(call.id),
                    "trace_ref": (
                        f"trace:{session_id}:{turn_id}:{call.id}"
                    ),
                }
                completed_audit_ref = self._append_audit(
                    action="tool.completed",
                    target=f"tool:{call.name}",
                    decision="allow" if tool_ok else "error",
                    reason_code=(
                        "tool_completed"
                        if tool_ok
                        else (tool_error_code or "tool_execution_error")
                    ),
                    session_id=str(session_id),
                    turn_id=str(turn_id),
                    call_id=call.id,
                    payload=completed_payload,
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
                context.tool_result_tokens = tool_result_tokens
                context.budget.consume(tool_calls=1)
                if call.name == "delegate_task" and context.delegate_call_completed_probe is not None:
                    context.delegate_call_completed_probe(
                        call.id,
                        {
                            "ok": tool_ok,
                            "data": result.data if tool_ok and isinstance(result.data, dict) else None,
                            "error_code": tool_error_code,
                        },
                    )
                if on_tool_event:
                    candidate_urls: list[str] = []
                    if call.name == "search_jobs_serpapi" and tool_ok and isinstance(result.data, dict):
                        for item in result.data.get("results", []):
                            if isinstance(item, dict):
                                candidate = sanitize_provenance_url(item.get("url"))
                                if candidate is not None:
                                    candidate_urls.append(candidate)
                    structured_job = (
                        _structured_job_from_snapshot_result(
                            result.data,
                            tool_metadata,
                            persisted_source_ref or guarded.raw_source_ref,
                        )
                        if call.name == "mcp__playwright__browser_snapshot"
                        and tool_ok
                        and isinstance(result.data, dict)
                        else None
                    )
                    await on_tool_event(
                        {
                            "type": "tool_completed",
                            "call_id": call.id,
                            "name": call.name,
                            "confirmation_id": execution_event_context.get(
                                "confirmation_id"
                            ),
                            "session_id": str(session_id),
                            "turn_id": str(turn_id),
                            "status": (
                                "completed"
                                if tool_ok
                                else (
                                    "cancelled"
                                    if tool_error_code
                                    == "tool_confirmation_cancelled"
                                    else (
                                        "expired"
                                        if tool_error_code
                                        == "tool_confirmation_timeout"
                                        else "failed"
                                    )
                                )
                            ),
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
                            "artifact_ref": tool_metadata.get("artifact_ref"),
                            "status_code": (
                                tool_metadata.get("status_code")
                                if isinstance(tool_metadata.get("status_code"), int)
                                and 100 <= tool_metadata["status_code"] <= 599
                                else None
                            ),
                            "structured_job": structured_job,
                            "evidence_refs": (
                                [
                                    {"chunk_id": item.get("chunk_id"), "source_ref": item.get("source_ref")}
                                    for item in result.data.get("evidence", [])
                                    if isinstance(item, dict)
                                    and isinstance(item.get("chunk_id"), str)
                                    and isinstance(item.get("source_ref"), str)
                                ]
                                if call.name == "retrieve_resume_evidence"
                                and tool_ok and isinstance(result.data, dict)
                                else []
                            ),
                            "candidate_urls": candidate_urls,
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
                            "tool_invoked": self._tool_was_invoked(call.id),
                            "audit_ref": completed_audit_ref,
                            "trace_ref": (
                                f"trace:{session_id}:{turn_id}:{call.id}"
                            ),
                        }
                    )

            # A Coordinator may create several durable Child Runs in one model
            # response.  Only after the complete batch has passed the normal
            # Tool/Gate path may it release its worker before another model call.
            if context.suspension_probe is not None:
                checkpoint_ref = context.suspension_probe(context)
                if checkpoint_ref is not None:
                    context.suspension_checkpoint_ref = checkpoint_ref
                    context.suspension_requested = True
                    return (
                        ModelResponse(
                            content=None,
                            provider=provider.name,
                            model=model,
                            usage=aggregate_usage(provider_usages),
                        ),
                        generated,
                        tool_calls,
                    )
            if context.boundary_stop_probe is not None:
                stop_reason = context.boundary_stop_probe(context)
                if stop_reason is not None:
                    context.boundary_stop_reason = stop_reason
                    return (
                        ModelResponse(
                            content="",
                            provider=provider.name,
                            model=model,
                            usage=aggregate_usage(provider_usages),
                        ),
                        generated,
                        tool_calls,
                    )

        raise RuntimeContinuationRequired(
            generated=generated,
            usage=aggregate_usage(provider_usages),
            tool_calls=tool_calls,
            model_calls=model_calls,
            context_revision=context_revision,
        )

    def _validate_pinned_tool_view(self, context: RunContext) -> None:
        if context.child_task_id is None:
            return
        snapshot = context.tool_schema_snapshot
        if snapshot is None:
            snapshot_reader = getattr(self.gate.registry, "model_snapshot", None)
            if callable(snapshot_reader):
                current_snapshot = snapshot_reader()
                allowed = set(context.effective_tool_view)
                snapshot = ModelToolSnapshot(
                    context_revision=current_snapshot.context_revision,
                    tools=tuple(
                        definition
                        for definition in current_snapshot.tools
                        if definition.get("function", {}).get("name") in allowed
                    ),
                )
                context.tool_schema_snapshot = snapshot
        expected_hashes = {
            item.get("function", {}).get("name"): canonical_json_sha256(
                item.get("function", {}).get("parameters", {})
            )
            for item in (() if snapshot is None else snapshot.provider_tools())
        }
        for name in context.effective_tool_view:
            capability = self.gate.registry.resolve_execution(name)
            if (
                capability is None
                or not capability.enabled
                or not capability.connected
                or capability.review_state != "approved"
                or capability.model_alias != name
                or expected_hashes.get(name) != capability.schema_hash
            ):
                self._append_audit(
                    action="model.context.snapshot",
                    target=f"tool:{name}",
                    decision="deny",
                    reason_code="runtime_tool_view_stale",
                    session_id=str(context.session_id),
                    turn_id=str(context.turn_id),
                    call_id=f"tool-view:{context.run_id}",
                    payload={
                        "run_id": context.run_id,
                        "child_task_id": context.child_task_id,
                        "expected_schema_hash": expected_hashes.get(name),
                        "current_schema_hash": (
                            None if capability is None else capability.schema_hash
                        ),
                    },
                )
                raise RunToolViewStale(name)


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
