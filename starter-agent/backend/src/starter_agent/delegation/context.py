from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from copy import deepcopy
from datetime import datetime
import json
from typing import Any, Callable, Literal, Protocol
from uuid import UUID

from starter_agent.delegation.models import BudgetLimits
from starter_agent.domain.models import Message
from starter_agent.tools.base import ToolContext
from starter_agent.delegation.models import RunSpec, TaskContract
from starter_agent.delegation.registry import SpecialistDefinition
from starter_agent.delegation.tool_view import EffectiveToolView, build_effective_tool_view
from starter_agent.capabilities.models import FrozenJsonDict
from starter_agent.capabilities.registry import ModelToolSnapshot
from starter_agent.orchestration.models import ExecutionState


class RunBudgetExceeded(ValueError):
    """A run-scoped budget dimension cannot accept more consumption."""


class RunToolViewStale(RuntimeError):
    """The pinned model schema no longer matches current execution authority."""


class ContextBuildError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ContextReference:
    kind: Literal["artifact", "knowledge_chunk", "source"]
    ref_id: str
    parent_run_id: str
    principal: str
    expires_at: datetime
    child_task_id: str | None = None
    child_run_id: str | None = None
    artifact_type: str | None = None
    knowledge_scope_type: str | None = None
    knowledge_user_id: str | None = None
    knowledge_project_id: str | None = None
    knowledge_base_id: str | None = None
    document_id: str | None = None
    chunk_id: str | None = None
    source_url: str | None = None
    content_hash: str | None = None


@dataclass(frozen=True, slots=True)
class ContextFragment:
    kind: str
    ref_id: str
    content: str
    artifact_type: str | None = None
    document_id: str | None = None
    chunk_id: str | None = None
    knowledge_user_id: str | None = None
    knowledge_project_id: str | None = None
    knowledge_base_id: str | None = None
    source_url: str | None = None
    content_hash: str | None = None
    untrusted: bool = True


@dataclass(frozen=True, slots=True)
class RuntimeContextAuthority:
    parent_run_id: str
    child_task_id: str
    child_run_id: str
    session_id: UUID
    turn_id: UUID
    principal: str
    now: datetime
    parent_deadline: datetime
    policy_deadline: datetime
    parent_remaining_budget: BudgetLimits
    policy_budget: BudgetLimits
    scenario_tools: frozenset[str]
    policy_tools: frozenset[str]
    allowed_artifact_types: frozenset[str]
    allowed_knowledge_scope_types: frozenset[str]
    knowledge_user_id: str | None
    knowledge_project_id: str | None
    knowledge_base_id: str | None
    runtime_revision: str
    provider: str
    model: str
    tool_registry: Any | None = None
    eval_run_id: str | None = None


class ContextReferenceResolver(Protocol):
    def load(
        self, reference: ContextReference, authority: RuntimeContextAuthority
    ) -> ContextFragment: ...


@dataclass(frozen=True, slots=True)
class BuiltChildContext:
    spec: RunSpec
    context: "RunContext"
    deadline: datetime
    tool_view: EffectiveToolView | None


def _minimum_budget(*budgets: BudgetLimits) -> BudgetLimits:
    return BudgetLimits(
        **{
            dimension: min(getattr(item, dimension) for item in budgets)
            for dimension in BudgetLimits.model_fields
        }
    )


_FORBIDDEN_CONTEXT_KEYS = frozenset(
    {
        "full_chat",
        "messages",
        "long_term_memory",
        "working_memory",
        "system_prompt",
        "system_prompt_ref",
        "output_schema",
        "allowed_tools",
        "tool_schema",
        "tool_schemas",
        "other_child_results",
    }
)


def _find_forbidden_context_key(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in _FORBIDDEN_CONTEXT_KEYS:
                return str(key)
            found = _find_forbidden_context_key(item)
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for item in value:
            found = _find_forbidden_context_key(item)
            if found is not None:
                return found
    return None


def _zero_budget() -> BudgetLimits:
    return BudgetLimits(
        tokens=0,
        cost_microunits=0,
        wall_clock_ms=0,
        model_calls=0,
        tool_calls=0,
    )


@dataclass(slots=True)
class RunBudgetState:
    limits: BudgetLimits
    consumed: BudgetLimits = field(default_factory=_zero_budget)
    overage: BudgetLimits = field(default_factory=_zero_budget)
    cost_unknown: bool = False

    def consume(self, **increments: int) -> None:
        values = self.consumed.model_dump(mode="python")
        overage = self.overage.model_dump(mode="python")
        exceeded: str | None = None
        for dimension, increment in increments.items():
            if dimension not in BudgetLimits.model_fields:
                raise ValueError(f"unknown budget dimension: {dimension}")
            if increment < 0:
                raise ValueError("budget consumption cannot be negative")
            observed = values[dimension] + increment
            limit = getattr(self.limits, dimension)
            if observed > limit:
                overage[dimension] += observed - limit
                values[dimension] = limit
                exceeded = exceeded or dimension
            else:
                values[dimension] = observed
        self.consumed = BudgetLimits(**values)
        self.overage = BudgetLimits(**overage)
        if exceeded is not None:
            raise RunBudgetExceeded(f"run budget exceeded: {exceeded}")

    def mark_cost_unknown(self) -> None:
        self.cost_unknown = True


@dataclass(slots=True)
class CancellationState:
    requested: bool = False
    reason: str | None = None
    version: int = 0

    def request(self, reason: str) -> None:
        self.requested = True
        self.reason = reason
        self.version += 1


@dataclass(frozen=True, slots=True)
class RunTraceContext:
    parent_run_id: str
    child_task_id: str | None = None
    child_run_id: str | None = None
    eval_run_id: str | None = None
    case_id: str | None = None
    model_request_id: str | None = None
    policy_decision_id: str | None = None
    approval_id: str | None = None


@dataclass(slots=True)
class RunContext:
    run_id: str
    parent_run_id: str
    session_id: UUID
    turn_id: UUID
    principal: str
    messages: list[Message]
    effective_tool_view: list[str]
    budget_limits: InitVar[BudgetLimits]
    trace_context: RunTraceContext
    effective_tool_view_enforced: bool = False
    child_task_id: str | None = None
    user_id: str | None = None
    project_id: str | None = None
    knowledge_base_id: UUID | None = None
    knowledge_scope: str | None = None
    working_memory: dict[str, Any] = field(default_factory=dict)
    todo_plan: list[dict[str, Any]] = field(default_factory=list)
    summary_trim_state: dict[str, Any] = field(default_factory=dict)
    output_buffer: list[str] = field(default_factory=list)
    artifact_refs: list[str] = field(default_factory=list)
    repeated_calls: dict[str, int] = field(default_factory=dict)
    provider_usages: list[dict[str, Any]] = field(default_factory=list)
    context_version: int = 0
    tool_result_tokens: int = 0
    budget: RunBudgetState = field(init=False)
    cancellation: CancellationState = field(default_factory=CancellationState)
    tool_view: EffectiveToolView | None = None
    tool_schema_snapshot: ModelToolSnapshot | None = None
    cancellation_probe: Callable[[], tuple[int, bool]] | None = None
    suspension_probe: Callable[["RunContext"], str | None] | None = None
    suspension_checkpoint_ref: str | None = None
    suspension_requested: bool = False
    delegate_batch_probe: Callable[[tuple[Any, ...]], None] | None = None
    delegate_call_completed_probe: Callable[[str, dict[str, Any]], None] | None = None
    boundary_stop_probe: Callable[["RunContext"], str | None] | None = None
    tool_preflight_probe: Callable[
        [Any, "RunContext"],
        tuple[str, str] | tuple[str, str, dict[str, Any]] | None,
    ] | None = None
    repeated_call_scope_probe: Callable[[Any, "RunContext"], str] | None = None
    boundary_stop_reason: str | None = None
    per_tool_timeout_seconds: float | None = None
    max_tool_calls_per_response: int | None = None
    deadline_at: datetime | None = None
    orchestration_state: ExecutionState | None = None

    def __post_init__(self, budget_limits: BudgetLimits) -> None:
        if self.parent_run_id != self.trace_context.parent_run_id:
            raise ValueError("RunContext and trace parent_run_id must match")
        if self.child_task_id != self.trace_context.child_task_id:
            raise ValueError("RunContext and trace child_task_id must match")
        if self.child_task_id is not None and self.trace_context.child_run_id is None:
            raise ValueError("trace child_run_id is required for Child RunContext")
        if self.trace_context.child_run_id not in {None, self.run_id}:
            raise ValueError("trace child_run_id must match RunContext run_id")
        self.messages = [message.model_copy(deep=True) for message in self.messages]
        self.effective_tool_view = list(self.effective_tool_view)
        self.working_memory = deepcopy(self.working_memory)
        self.todo_plan = deepcopy(self.todo_plan)
        self.summary_trim_state = deepcopy(self.summary_trim_state)
        self.output_buffer = list(self.output_buffer)
        self.artifact_refs = list(self.artifact_refs)
        self.repeated_calls = dict(self.repeated_calls)
        self.provider_usages = deepcopy(self.provider_usages)
        if self.orchestration_state is not None:
            self.orchestration_state = self.orchestration_state.model_copy(deep=True)
            if self.orchestration_state.run_id != self.run_id:
                raise ValueError("orchestration_state_run_mismatch")
            if self.orchestration_state.session_id != str(self.session_id):
                raise ValueError("orchestration_state_session_mismatch")
            if self.orchestration_state.turn_id != str(self.turn_id):
                raise ValueError("orchestration_state_turn_mismatch")
        self.budget = RunBudgetState(budget_limits)

    def tool_context(
        self,
        tool_call_id: str | None = None,
        *,
        run_role: str | None = None,
    ) -> ToolContext:
        return ToolContext(
            session_id=self.session_id,
            turn_id=self.turn_id,
            tool_call_id=tool_call_id,
            user_id=self.user_id,
            project_id=self.project_id,
            knowledge_base_id=self.knowledge_base_id,
            parent_run_id=self.parent_run_id,
            child_task_id=self.child_task_id,
            child_run_id=self.trace_context.child_run_id,
            eval_run_id=self.trace_context.eval_run_id,
            case_id=self.trace_context.case_id,
            model_request_id=self.trace_context.model_request_id,
            policy_decision_id=self.trace_context.policy_decision_id,
            approval_id=self.trace_context.approval_id,
            knowledge_scope=self.knowledge_scope,
            run_role=run_role,
        )

    def refresh_cancellation(self) -> bool:
        """Refresh durable Parent cancellation at a cooperative boundary."""
        if self.cancellation_probe is None:
            return self.cancellation.requested
        version, requested = self.cancellation_probe()
        if version > self.cancellation.version:
            self.cancellation.version = version
        if requested:
            self.cancellation.requested = True
            self.cancellation.reason = self.cancellation.reason or "parent_cancelled"
        return self.cancellation.requested

    def to_checkpoint(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "parent_run_id": self.parent_run_id,
            "child_task_id": self.child_task_id,
            "session_id": str(self.session_id),
            "turn_id": str(self.turn_id),
            "principal": self.principal,
            "messages": [item.model_dump(mode="json") for item in self.messages],
            "effective_tool_view": list(self.effective_tool_view),
            "effective_tool_view_enforced": self.effective_tool_view_enforced,
            "tool_schema_snapshot": (
                None
                if self.tool_schema_snapshot is None and self.tool_view is None
                else {
                    "context_revision": (
                        self.tool_schema_snapshot.context_revision
                        if self.tool_schema_snapshot is not None
                        else self.tool_view.snapshot.context_revision
                    ),
                    "tools": (
                        self.tool_schema_snapshot.provider_tools()
                        if self.tool_schema_snapshot is not None
                        else self.tool_view.snapshot.provider_tools()
                    ),
                }
            ),
            "budget_limits": self.budget.limits.model_dump(mode="json"),
            "budget_consumed": self.budget.consumed.model_dump(mode="json"),
            "budget_overage": self.budget.overage.model_dump(mode="json"),
            "budget_cost_unknown": self.budget.cost_unknown,
            "trace_context": {
                name: getattr(self.trace_context, name)
                for name in self.trace_context.__dataclass_fields__
            },
            "user_id": self.user_id,
            "project_id": self.project_id,
            "knowledge_base_id": (
                None if self.knowledge_base_id is None else str(self.knowledge_base_id)
            ),
            "knowledge_scope": self.knowledge_scope,
            "working_memory": deepcopy(self.working_memory),
            "todo_plan": deepcopy(self.todo_plan),
            "summary_trim_state": deepcopy(self.summary_trim_state),
            "output_buffer": list(self.output_buffer),
            "artifact_refs": list(self.artifact_refs),
            "repeated_calls": dict(self.repeated_calls),
            "provider_usages": deepcopy(self.provider_usages),
            "context_version": self.context_version,
            "tool_result_tokens": self.tool_result_tokens,
            "cancellation": {
                "requested": self.cancellation.requested,
                "reason": self.cancellation.reason,
                "version": self.cancellation.version,
            },
            "suspension_checkpoint_ref": self.suspension_checkpoint_ref,
            "suspension_requested": self.suspension_requested,
            "boundary_stop_reason": self.boundary_stop_reason,
            "per_tool_timeout_seconds": self.per_tool_timeout_seconds,
            "deadline_at": None if self.deadline_at is None else self.deadline_at.isoformat(),
            "orchestration_state": (
                None
                if self.orchestration_state is None
                else self.orchestration_state.model_dump(mode="json")
            ),
        }

    @classmethod
    def from_checkpoint(cls, checkpoint: dict[str, Any]) -> "RunContext":
        context = cls(
            run_id=checkpoint["run_id"],
            parent_run_id=checkpoint["parent_run_id"],
            child_task_id=checkpoint.get("child_task_id"),
            session_id=UUID(checkpoint["session_id"]),
            turn_id=UUID(checkpoint["turn_id"]),
            principal=checkpoint["principal"],
            messages=[Message.model_validate(item) for item in checkpoint["messages"]],
            effective_tool_view=list(checkpoint["effective_tool_view"]),
            effective_tool_view_enforced=bool(checkpoint.get("effective_tool_view_enforced", False)),
            budget_limits=BudgetLimits.model_validate(checkpoint["budget_limits"]),
            trace_context=RunTraceContext(**checkpoint["trace_context"]),
            user_id=checkpoint.get("user_id"),
            project_id=checkpoint.get("project_id"),
            knowledge_base_id=(
                None
                if checkpoint.get("knowledge_base_id") is None
                else UUID(checkpoint["knowledge_base_id"])
            ),
            knowledge_scope=checkpoint.get("knowledge_scope"),
            working_memory=checkpoint.get("working_memory", {}),
            todo_plan=checkpoint.get("todo_plan", []),
            summary_trim_state=checkpoint.get("summary_trim_state", {}),
            output_buffer=checkpoint.get("output_buffer", []),
            artifact_refs=checkpoint.get("artifact_refs", []),
            repeated_calls=checkpoint.get("repeated_calls", {}),
            provider_usages=list(checkpoint.get("provider_usages", [])),
            context_version=checkpoint.get("context_version", 0),
            tool_result_tokens=checkpoint.get("tool_result_tokens", 0),
            cancellation=CancellationState(**checkpoint.get("cancellation", {})),
            suspension_checkpoint_ref=checkpoint.get("suspension_checkpoint_ref"),
            suspension_requested=bool(checkpoint.get("suspension_requested", False)),
            boundary_stop_reason=checkpoint.get("boundary_stop_reason"),
            per_tool_timeout_seconds=checkpoint.get("per_tool_timeout_seconds"),
            deadline_at=(None if checkpoint.get("deadline_at") is None else datetime.fromisoformat(checkpoint["deadline_at"])),
            orchestration_state=(
                None
                if checkpoint.get("orchestration_state") is None
                else ExecutionState.model_validate(checkpoint["orchestration_state"])
            ),
        )
        context.budget.consumed = BudgetLimits.model_validate(
            checkpoint.get("budget_consumed", _zero_budget().model_dump())
        )
        context.budget.overage = BudgetLimits.model_validate(
            checkpoint.get("budget_overage", _zero_budget().model_dump())
        )
        context.budget.cost_unknown = bool(checkpoint.get("budget_cost_unknown", False))
        tool_snapshot = checkpoint.get("tool_schema_snapshot")
        if tool_snapshot is not None:
            context.tool_schema_snapshot = ModelToolSnapshot(
                context_revision=int(tool_snapshot["context_revision"]),
                tools=tuple(FrozenJsonDict(item) for item in tool_snapshot["tools"]),
            )
        for dimension in BudgetLimits.model_fields:
            if getattr(context.budget.consumed, dimension) > getattr(
                context.budget.limits, dimension
            ):
                raise RunBudgetExceeded(f"run budget exceeded: {dimension}")
        return context


class ChildContextBuilder:
    def __init__(
        self,
        resolver: ContextReferenceResolver,
        *,
        audit_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.resolver = resolver
        self.audit_sink = audit_sink

    def build(
        self,
        contract: TaskContract,
        specialist: SpecialistDefinition,
        authority: RuntimeContextAuthority,
        *,
        references: tuple[ContextReference, ...],
    ) -> BuiltChildContext:
        self._validate_identity(contract, specialist, authority)
        forbidden = _find_forbidden_context_key(
            {"inputs": contract.inputs, "constraints": contract.constraints}
        )
        if forbidden is not None:
            raise ContextBuildError(
                "context_payload_forbidden",
                f"Coordinator payload cannot inject {forbidden}",
            )
        budget = _minimum_budget(
            contract.requested_budget,
            specialist.max_budget,
            authority.policy_budget,
            authority.parent_remaining_budget,
        )
        if (
            budget.tokens == 0
            or budget.cost_microunits == 0
            or budget.wall_clock_ms == 0
            or budget.model_calls == 0
        ):
            raise ContextBuildError(
                "context_budget_exhausted", "effective model budget is exhausted"
            )
        deadline = min(
            contract.requested_deadline,
            authority.parent_deadline,
            authority.policy_deadline,
            authority.now
            + __import__("datetime").timedelta(
                milliseconds=specialist.default_deadline_ms
            ),
        )
        if deadline <= authority.now:
            raise ContextBuildError("context_deadline_exhausted", "deadline is exhausted")

        tool_view = None
        allowed_names: tuple[str, ...]
        if authority.tool_registry is None:
            raise ContextBuildError(
                "context_tool_registry_unavailable",
                "shared callable Tool Registry is required",
            )
        tool_view = build_effective_tool_view(
            authority.tool_registry,
            registry_allowed=specialist.allowed_tools,
            contract_requested=contract.requested_allowed_tools,
            scenario_allowed=authority.scenario_tools,
            policy_allowed=authority.policy_tools,
        )
        allowed_names = tool_view.names

        fragments: list[ContextFragment] = []
        for reference in references:
            try:
                self._validate_reference_authority(reference, specialist, authority)
                fragment = self.resolver.load(reference, authority)
                self._validate_fragment_reference(
                    reference, fragment, specialist, authority
                )
            except ContextBuildError as exc:
                self._audit_reference(reference, authority, "deny", exc.code)
                raise
            fragments.append(fragment)
            self._audit_reference(reference, authority, "allow", "context_reference_loaded")

        knowledge_scopes = {
            item.knowledge_scope_type
            for item in references
            if item.kind == "knowledge_chunk" and item.knowledge_scope_type is not None
        }
        requested_scope = contract.inputs.get("knowledge_scope")
        if specialist.specialist_id == "profile_evidence_analyst":
            if (
                not isinstance(requested_scope, dict)
                or requested_scope.get("type") != "resume"
                or requested_scope.get("user_id") != authority.knowledge_user_id
                or requested_scope.get("project_id") != authority.knowledge_project_id
                or requested_scope.get("knowledge_base_id") != authority.knowledge_base_id
            ):
                raise ContextBuildError(
                    "profile_knowledge_binding_unavailable",
                    "profile knowledge scope is not bound to the authenticated child authority",
                )
            knowledge_scopes.add("resume")
        context = RunContext(
            run_id=authority.child_run_id,
            parent_run_id=authority.parent_run_id,
            child_task_id=authority.child_task_id,
            session_id=authority.session_id,
            turn_id=authority.turn_id,
            principal=authority.principal,
            messages=[
                Message(role="system", content=specialist.system_prompt),
                Message(
                    role="user",
                    content=json.dumps(
                        {
                            "goal": contract.goal,
                            "inputs": contract.inputs,
                            "constraints": contract.constraints,
                            "failure_behavior": contract.failure_behavior,
                            "context_refs": [item.ref_id for item in fragments],
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                ),
            ],
            effective_tool_view=list(allowed_names),
            budget_limits=budget,
            trace_context=RunTraceContext(
                parent_run_id=authority.parent_run_id,
                child_task_id=authority.child_task_id,
                child_run_id=authority.child_run_id,
                eval_run_id=authority.eval_run_id,
            ),
            deadline_at=deadline,
            user_id=authority.knowledge_user_id,
            project_id=authority.knowledge_project_id,
            knowledge_base_id=(
                None
                if authority.knowledge_base_id is None
                else UUID(authority.knowledge_base_id)
            ),
            knowledge_scope=(
                next(iter(knowledge_scopes)) if len(knowledge_scopes) == 1 else None
            ),
            working_memory={
                "context_fragments": [
                    {
                        "kind": item.kind,
                        "ref_id": item.ref_id,
                        "content": item.content,
                        "source_url": item.source_url,
                        "content_hash": item.content_hash,
                        "untrusted": True,
                    }
                    for item in fragments
                ],
                "deadline": deadline.isoformat(),
            },
            tool_view=tool_view,
            tool_schema_snapshot=tool_view.model_snapshot(),
        )
        spec = RunSpec(
            run_id=authority.child_run_id,
            run_kind="child",
            role="specialist",
            provider=authority.provider,
            model=authority.model,
            system_prompt_ref=specialist.system_prompt_ref,
            output_schema_ref=f"specialist-schema:{specialist.schema_version}",
            allowed_tools=allowed_names,
            max_steps=min(specialist.max_steps, budget.model_calls),
            runtime_revision=authority.runtime_revision,
            stop_policy={"deadline": deadline.isoformat()},
        )
        return BuiltChildContext(spec, context, deadline, tool_view)

    @staticmethod
    def _validate_identity(
        contract: TaskContract,
        specialist: SpecialistDefinition,
        authority: RuntimeContextAuthority,
    ) -> None:
        if contract.parent_run_id != authority.parent_run_id:
            raise ContextBuildError("context_identity_mismatch", "parent mismatch")
        if contract.task_id != authority.child_task_id:
            raise ContextBuildError("context_identity_mismatch", "task mismatch")
        if contract.specialist_id != specialist.specialist_id:
            raise ContextBuildError("context_identity_mismatch", "specialist mismatch")

    @staticmethod
    def _validate_reference_authority(
        reference: ContextReference,
        specialist: SpecialistDefinition,
        authority: RuntimeContextAuthority,
    ) -> None:
        if reference.parent_run_id != authority.parent_run_id:
            raise ContextBuildError("context_reference_forbidden", "run mismatch")
        if reference.principal != authority.principal:
            raise ContextBuildError("context_reference_forbidden", "principal mismatch")
        if reference.child_task_id != authority.child_task_id:
            raise ContextBuildError("context_reference_forbidden", "task mismatch")
        if reference.child_run_id != authority.child_run_id:
            raise ContextBuildError("context_reference_forbidden", "child run mismatch")
        if reference.expires_at <= authority.now:
            raise ContextBuildError("context_reference_expired", "reference expired")
        allowed_artifacts = set(specialist.allowed_artifact_types) & set(
            authority.allowed_artifact_types
        )
        if reference.kind == "artifact" and reference.artifact_type not in allowed_artifacts:
            raise ContextBuildError("context_reference_forbidden", "artifact type denied")
        allowed_scopes = set(specialist.allowed_knowledge_scope_types) & set(
            authority.allowed_knowledge_scope_types
        )
        if reference.kind == "knowledge_chunk":
            if reference.knowledge_scope_type not in allowed_scopes:
                raise ContextBuildError("context_reference_forbidden", "scope denied")
            if not reference.document_id or not reference.chunk_id:
                raise ContextBuildError("context_reference_invalid", "chunk identity required")
            if (
                reference.knowledge_user_id != authority.knowledge_user_id
                or reference.knowledge_project_id != authority.knowledge_project_id
                or reference.knowledge_base_id != authority.knowledge_base_id
            ):
                raise ContextBuildError("context_reference_forbidden", "knowledge scope mismatch")
        if reference.kind == "source" and (
            not reference.source_url or not reference.content_hash
        ):
            raise ContextBuildError("context_reference_invalid", "source proof required")

    @classmethod
    def _validate_fragment_reference(
        cls,
        reference: ContextReference,
        fragment: ContextFragment,
        specialist: SpecialistDefinition,
        authority: RuntimeContextAuthority,
    ) -> None:
        cls._validate_reference_authority(reference, specialist, authority)
        if fragment.ref_id != reference.ref_id:
            raise ContextBuildError("context_reference_invalid", "resolver ref mismatch")
        if fragment.kind != reference.kind:
            raise ContextBuildError("context_reference_invalid", "resolver kind mismatch")
        if fragment.artifact_type != reference.artifact_type:
            raise ContextBuildError("context_reference_invalid", "artifact type mismatch")
        if fragment.document_id != reference.document_id:
            raise ContextBuildError("context_reference_invalid", "document mismatch")
        if fragment.chunk_id != reference.chunk_id:
            raise ContextBuildError("context_reference_invalid", "chunk mismatch")
        if fragment.knowledge_user_id != reference.knowledge_user_id:
            raise ContextBuildError("context_reference_invalid", "knowledge user mismatch")
        if fragment.knowledge_project_id != reference.knowledge_project_id:
            raise ContextBuildError("context_reference_invalid", "knowledge project mismatch")
        if fragment.knowledge_base_id != reference.knowledge_base_id:
            raise ContextBuildError("context_reference_invalid", "knowledge base mismatch")
        if reference.source_url is not None and fragment.source_url != reference.source_url:
            raise ContextBuildError("context_reference_invalid", "source URL mismatch")
        if (
            reference.content_hash is not None
            and fragment.content_hash != reference.content_hash
        ):
            raise ContextBuildError("context_reference_invalid", "content hash mismatch")

    def _audit_reference(
        self,
        reference: ContextReference,
        authority: RuntimeContextAuthority,
        decision: str,
        reason_code: str,
    ) -> None:
        if self.audit_sink is not None:
            self.audit_sink(
                {
                    "action": "context.reference.load",
                    "decision": decision,
                    "reason_code": reason_code,
                    "parent_run_id": authority.parent_run_id,
                    "child_task_id": authority.child_task_id,
                    "child_run_id": authority.child_run_id,
                    "ref_id": reference.ref_id,
                    "kind": reference.kind,
                }
            )
