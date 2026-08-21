from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from starter_agent.capabilities.models import (
    BoundedJsonObject,
    Identifier,
    Sha256,
    UtcDateTime,
    canonical_json_sha256,
)


RunStatus = Literal[
    "created",
    "queued",
    "running",
    "waiting_children",
    "waiting_for_user",
    "cancelling",
    "succeeded",
    "partial",
    "failed",
    "timed_out",
    "budget_exhausted",
    "cancelled",
]
TerminalRunStatus = Literal[
    "succeeded",
    "partial",
    "failed",
    "timed_out",
    "budget_exhausted",
    "cancelled",
]
FailureBehavior = Literal["fail_parent", "allow_partial", "wait_for_user"]
BudgetDimension = Literal[
    "steps",
    "tokens",
    "cost_microunits",
    "wall_clock_ms",
    "model_calls",
    "tool_calls",
]
ResultStatus = Literal["succeeded", "partial", "failed"]

ShortText = Annotated[str, Field(min_length=1, max_length=200)]
LongText = Annotated[str, Field(min_length=1, max_length=4_000)]
Reference = Annotated[str, Field(min_length=1, max_length=500)]
SQLITE_INTEGER_MAX = 2**63 - 1
NonNegativeInt = Annotated[int, Field(ge=0, le=SQLITE_INTEGER_MAX)]
PositiveInt = Annotated[int, Field(ge=1, le=SQLITE_INTEGER_MAX)]

_TERMINAL_STATUSES = frozenset(
    {"succeeded", "partial", "failed", "timed_out", "budget_exhausted", "cancelled"}
)
_COMMON_TRANSITIONS: dict[str, frozenset[str]] = {
    "created": frozenset({"queued", "cancelling"}),
    "queued": frozenset({"running", "cancelling"}),
    "running": frozenset(
        {
            "waiting_for_user",
            "cancelling",
            "succeeded",
            "partial",
            "failed",
            "timed_out",
            "budget_exhausted",
        }
    ),
    "waiting_for_user": frozenset({"queued", "cancelling"}),
    "cancelling": frozenset({"cancelled"}),
}
_PARENT_TRANSITIONS = {
    **_COMMON_TRANSITIONS,
    "running": _COMMON_TRANSITIONS["running"] | {"waiting_children"},
    "waiting_children": frozenset({"queued", "cancelling"}),
}
_UTC_DATE_TIME_ADAPTER = TypeAdapter(UtcDateTime)


class DelegationModelError(ValueError):
    """Stable domain failure raised before persistence is involved."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DelegationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class BudgetLimits(DelegationModel):
    # ``steps`` was added by orchestration schema v1.  A zero default keeps
    # legacy five-dimensional payloads readable; orchestration callers must
    # supply an explicit non-zero step allowance before obtaining a permit.
    steps: NonNegativeInt = 0
    tokens: NonNegativeInt
    cost_microunits: NonNegativeInt
    wall_clock_ms: NonNegativeInt
    model_calls: NonNegativeInt
    tool_calls: NonNegativeInt


class BudgetUsage(BudgetLimits):
    estimated: bool = False
    cost_status: Literal["actual", "estimated", "unknown"]
    price_version: str | None = Field(default=None, min_length=1, max_length=120)
    usage_source: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def validate_cost_status(self) -> "BudgetUsage":
        if self.cost_status == "estimated" and not self.estimated:
            raise ValueError("estimated cost status requires estimated usage")
        if self.estimated and self.cost_status != "estimated":
            raise ValueError("estimated usage requires estimated cost status")
        return self


class BudgetAllocation(DelegationModel):
    dimension: BudgetDimension
    limit: NonNegativeInt
    requested: NonNegativeInt
    reserved: NonNegativeInt
    consumed: NonNegativeInt
    released: NonNegativeInt
    estimated: bool = False
    price_version: str | None = Field(default=None, min_length=1, max_length=120)
    usage_source: str | None = Field(default=None, min_length=1, max_length=120)
    version: NonNegativeInt = 0

    @model_validator(mode="after")
    def validate_accounting(self) -> "BudgetAllocation":
        if self.requested > self.limit:
            raise ValueError("requested cannot exceed limit")
        if self.reserved > self.requested:
            raise ValueError("reserved cannot exceed requested")
        if self.consumed + self.released > self.reserved:
            raise ValueError("consumed and released cannot exceed reserved")
        return self


class TaskContract(DelegationModel):
    task_id: Identifier
    parent_run_id: Identifier
    specialist_id: Identifier
    goal: LongText
    inputs: BoundedJsonObject
    constraints: BoundedJsonObject = Field(default_factory=dict)
    requested_allowed_tools: tuple[Identifier, ...] = Field(
        default_factory=tuple,
        max_length=64,
    )
    requested_deadline: UtcDateTime
    requested_budget: BudgetLimits
    failure_behavior: FailureBehavior
    idempotency_key: Identifier
    contract_version: str = Field(default="1", min_length=1, max_length=32)

    @field_validator("requested_allowed_tools")
    @classmethod
    def normalize_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("requested_allowed_tools must be unique")
        return tuple(sorted(value))

    @property
    def canonical_hash(self) -> str:
        return canonical_json_sha256(self.model_dump(mode="json"))


class DelegationReceipt(DelegationModel):
    """Controlled acknowledgement that a real Child Run was persisted."""

    receipt_id: Identifier
    parent_run_id: Identifier
    task_id: Identifier
    child_run_id: Identifier
    specialist_id: Identifier
    specialist_snapshot_id: Identifier
    status: Literal["queued"] = "queued"
    created_at: UtcDateTime


class ParentRun(DelegationModel):
    id: Identifier
    run_type: Literal[
        "job_application_research", "job_application_orchestration"
    ] = "job_application_research"
    session_id: Identifier
    origin_turn_id: Identifier
    principal: ShortText
    coordinator_spec_version: ShortText
    runtime_revision: ShortText
    status: RunStatus = "created"
    phase: ShortText = "created"
    version: NonNegativeInt = 0
    priority: int = Field(default=100, ge=0, le=1_000)
    available_at: UtcDateTime
    deadline_at: UtcDateTime
    cancellation_version: NonNegativeInt = 0
    cancel_requested_at: UtcDateTime | None = None
    cancelled_at: UtcDateTime | None = None
    budget_total: BudgetLimits
    budget_reserved: BudgetLimits
    budget_consumed: BudgetLimits
    result_version: NonNegativeInt = 0
    merge_report_id: Identifier | None = None
    backfill_status: Literal["not_ready", "pending", "completed", "failed"] = "not_ready"
    backfill_message_id: Identifier | None = None
    route: ShortText
    legacy_path_used: bool = False
    task_id: Identifier | None = None
    request_hash: Sha256 | None = None
    route_decision_id: Identifier | None = None
    orchestration_state_version: NonNegativeInt = 0
    orchestration_state: BoundedJsonObject | None = None
    plan_id: Identifier | None = None
    current_step_id: Identifier | None = None
    join_policy: ShortText | None = None
    pending_action_id: Identifier | None = None
    latest_budget_snapshot_id: Identifier | None = None
    stop_reason_code: ShortText | None = None
    created_at: UtcDateTime
    started_at: UtcDateTime | None = None
    completed_at: UtcDateTime | None = None
    updated_at: UtcDateTime

    @model_validator(mode="after")
    def validate_run_invariants(self) -> "ParentRun":
        _validate_run_timestamps(self)
        if self.available_at > self.deadline_at:
            raise ValueError("available_at cannot follow deadline_at")
        if self.status in {"cancelling", "cancelled"} and self.cancel_requested_at is None:
            raise ValueError("cancelling runs require cancel_requested_at")
        _validate_budget_summary(
            total=self.budget_total,
            reserved=self.budget_reserved,
            consumed=self.budget_consumed,
        )
        return self


class ChildTask(DelegationModel):
    id: Identifier
    parent_run_id: Identifier
    specialist_id: Identifier
    specialist_snapshot_id: Identifier
    goal: LongText
    inputs_ref_json: BoundedJsonObject
    constraints_json: BoundedJsonObject
    output_schema_version: ShortText
    requested_allowed_tools: tuple[Identifier, ...] = Field(max_length=64)
    requested_deadline: UtcDateTime
    requested_budget: BudgetLimits
    failure_behavior: FailureBehavior
    idempotency_key: Identifier
    contract_hash: Sha256
    contract_version: str = Field(min_length=1, max_length=32)
    status: RunStatus = "created"
    version: NonNegativeInt = 0
    accepted_child_run_id: Identifier | None = None
    accepted_result_envelope_ref: Reference | None = None
    accepted_result_hash: Sha256 | None = None
    accepted_at: UtcDateTime | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime

    @model_validator(mode="after")
    def validate_accepted_result(self) -> "ChildTask":
        values = (
            self.accepted_child_run_id,
            self.accepted_result_envelope_ref,
            self.accepted_result_hash,
            self.accepted_at,
        )
        if any(value is not None for value in values) and not all(
            value is not None for value in values
        ):
            raise ValueError("accepted result fields must be set together")
        if self.accepted_at is not None:
            if self.accepted_at < self.created_at:
                raise ValueError("accepted_at cannot precede created_at")
            if self.accepted_at > self.updated_at:
                raise ValueError("accepted_at cannot follow updated_at")
        return self

    @classmethod
    def from_contract(
        cls,
        contract: TaskContract,
        *,
        specialist_snapshot_id: str,
        output_schema_version: str,
        created_at: datetime,
    ) -> "ChildTask":
        return cls(
            id=contract.task_id,
            parent_run_id=contract.parent_run_id,
            specialist_id=contract.specialist_id,
            specialist_snapshot_id=specialist_snapshot_id,
            goal=contract.goal,
            inputs_ref_json=contract.inputs,
            constraints_json=contract.constraints,
            output_schema_version=output_schema_version,
            requested_allowed_tools=contract.requested_allowed_tools,
            requested_deadline=contract.requested_deadline,
            requested_budget=contract.requested_budget,
            failure_behavior=contract.failure_behavior,
            idempotency_key=contract.idempotency_key,
            contract_hash=contract.canonical_hash,
            contract_version=contract.contract_version,
            created_at=created_at,
            updated_at=created_at,
        )


class ChildRun(DelegationModel):
    id: Identifier
    child_task_id: Identifier
    parent_run_id: Identifier
    attempt: PositiveInt
    status: RunStatus = "created"
    phase: ShortText = "created"
    version: NonNegativeInt = 0
    lease_owner: Identifier | None = None
    lease_token: Identifier | None = None
    lease_expires_at: UtcDateTime | None = None
    heartbeat_at: UtcDateTime | None = None
    deadline_at: UtcDateTime
    available_at: UtcDateTime | None = None
    run_context_checkpoint_ref: Reference | None = None
    effective_tool_view_hash: Sha256 | None = None
    result_envelope_ref: Reference | None = None
    result_hash: Sha256 | None = None
    error_code: str | None = Field(default=None, min_length=1, max_length=160)
    retryable: bool = False
    created_at: UtcDateTime
    started_at: UtcDateTime | None = None
    cancelled_at: UtcDateTime | None = None
    completed_at: UtcDateTime | None = None
    updated_at: UtcDateTime

    @model_validator(mode="after")
    def child_cannot_wait_for_children(self) -> "ChildRun":
        if self.status == "waiting_children":
            raise ValueError("child runs cannot wait for child runs")
        _validate_run_timestamps(self)
        if self.available_at is not None and self.available_at < self.created_at:
            raise ValueError("available_at cannot precede created_at")
        lease_values = (
            self.lease_owner,
            self.lease_token,
            self.lease_expires_at,
            self.heartbeat_at,
        )
        if any(item is not None for item in lease_values) and not all(
            item is not None for item in lease_values
        ):
            raise ValueError("child lease fields must be set or cleared together")
        if all(item is not None for item in lease_values):
            if self.status != "running":
                raise ValueError("only a running child lease may be active")
            if self.lease_expires_at < self.heartbeat_at:
                raise ValueError("lease expiry cannot precede heartbeat")
        return self


class RunSpec(DelegationModel):
    run_id: Identifier
    run_kind: Literal["parent", "child"]
    role: Literal["coordinator", "specialist"]
    provider: ShortText
    model: ShortText
    system_prompt_ref: Reference
    output_schema_ref: Reference
    allowed_tools: tuple[Identifier, ...] = Field(default_factory=tuple, max_length=64)
    max_steps: int = Field(ge=1, le=1_000)
    runtime_revision: ShortText
    stop_policy: BoundedJsonObject = Field(default_factory=dict)

    @field_validator("allowed_tools")
    @classmethod
    def normalize_allowed_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("allowed_tools must be unique")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def validate_role_matches_run_kind(self) -> "RunSpec":
        expected_role = "coordinator" if self.run_kind == "parent" else "specialist"
        if self.role != expected_role:
            raise ValueError("run_kind and role must describe the same execution role")
        return self


class RunOutcome(DelegationModel):
    disposition: Literal["completed", "suspended", "failed", "cancelled"]
    run_id: Identifier
    status: RunStatus
    output_ref: Reference | None = None
    result_envelope_ref: Reference | None = None
    result_envelope_hash: Sha256 | None = None
    checkpoint_ref: Reference | None = None
    error_code: str | None = Field(default=None, min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_disposition_matches_status(self) -> "RunOutcome":
        allowed_statuses = {
            "completed": {"succeeded", "partial"},
            "suspended": {"waiting_children", "waiting_for_user"},
            "failed": {"failed", "timed_out", "budget_exhausted"},
            "cancelled": {"cancelled"},
        }
        if self.status not in allowed_statuses[self.disposition]:
            raise ValueError("disposition does not match status")
        return self


class ResultEnvelope(DelegationModel):
    envelope_version: str = Field(default="1", min_length=1, max_length=32)
    status: ResultStatus
    output: BoundedJsonObject
    evidence: tuple[BoundedJsonObject, ...] = Field(max_length=2_000)
    missing: tuple[Annotated[str, Field(min_length=1, max_length=500)], ...] = Field(
        max_length=1_000
    )
    conflicts: tuple[BoundedJsonObject, ...] = Field(max_length=1_000)
    errors: tuple[BoundedJsonObject, ...] = Field(default_factory=tuple, max_length=1_000)
    usage: BudgetUsage
    child_run_id: Identifier
    task_id: Identifier
    trace_ref: Reference
    idempotency_key: Identifier

    @property
    def canonical_hash(self) -> str:
        return canonical_json_sha256(self.model_dump(mode="json"))


class MergeReport(DelegationModel):
    id: Identifier
    parent_run_id: Identifier
    result_version: PositiveInt
    input_envelope_refs: tuple[Reference, ...] = Field(max_length=1_000)
    input_hashes: tuple[Sha256, ...] = Field(max_length=1_000)
    accepted: tuple[BoundedJsonObject, ...] = Field(max_length=1_000)
    rejected: tuple[BoundedJsonObject, ...] = Field(max_length=1_000)
    dedup_groups: tuple[BoundedJsonObject, ...] = Field(max_length=1_000)
    missing: tuple[Annotated[str, Field(min_length=1, max_length=500)], ...] = Field(
        max_length=1_000
    )
    conflicts: tuple[BoundedJsonObject, ...] = Field(max_length=1_000)
    source_validation: tuple[BoundedJsonObject, ...] = Field(max_length=2_000)
    evidence_validation: tuple[BoundedJsonObject, ...] = Field(max_length=2_000)
    ranking_features: BoundedJsonObject
    deterministic_order: tuple[Identifier, ...] = Field(max_length=2_000)
    semantic_synthesis_version: ShortText
    final_output_ref: Reference
    final_output_hash: Sha256
    created_at: UtcDateTime


IdempotentModel = TypeVar("IdempotentModel", TaskContract, ResultEnvelope)


def ensure_idempotency_compatible(
    existing: IdempotentModel,
    candidate: IdempotentModel,
) -> IdempotentModel:
    if existing.idempotency_key != candidate.idempotency_key:
        return candidate
    if type(existing) is not type(candidate) or existing.canonical_hash != candidate.canonical_hash:
        raise DelegationModelError(
            "idempotency_payload_conflict",
            "the idempotency key is already bound to a different payload",
        )
    return existing


RunModel = TypeVar("RunModel", ParentRun, ChildRun)


def _validate_run_timestamps(run: ParentRun | ChildRun) -> None:
    if run.updated_at < run.created_at:
        raise ValueError("updated_at cannot precede created_at")
    if run.deadline_at < run.created_at:
        raise ValueError("deadline_at cannot precede created_at")
    if run.started_at is not None and run.started_at < run.created_at:
        raise ValueError("started_at cannot precede created_at")
    started_required = {
        "running",
        "waiting_children",
        "waiting_for_user",
        "succeeded",
        "partial",
        "failed",
        "timed_out",
        "budget_exhausted",
    }
    if run.status in started_required and run.started_at is None:
        raise ValueError(f"run status {run.status} requires started_at")
    if run.started_at is not None and run.started_at > run.updated_at:
        raise ValueError("started_at cannot follow updated_at")
    completion_floor = run.started_at or run.created_at
    if run.completed_at is not None and run.completed_at < completion_floor:
        raise ValueError("completed_at cannot precede started_at")
    if run.completed_at is not None and run.completed_at > run.updated_at:
        raise ValueError("completed_at cannot follow updated_at")
    if run.status in _TERMINAL_STATUSES and run.completed_at is None:
        raise ValueError("terminal runs require completed_at")
    if run.status not in _TERMINAL_STATUSES and run.completed_at is not None:
        raise ValueError("non-terminal runs cannot have completed_at")
    if run.status == "cancelled" and run.cancelled_at is None:
        raise ValueError("cancelled runs require cancelled_at")
    if run.status != "cancelled" and run.cancelled_at is not None:
        raise ValueError("only cancelled runs may have cancelled_at")
    if run.cancelled_at is not None and run.cancelled_at < completion_floor:
        raise ValueError("cancelled_at cannot precede started_at")
    if run.cancelled_at is not None and run.cancelled_at > run.updated_at:
        raise ValueError("cancelled_at cannot follow updated_at")
    if isinstance(run, ParentRun) and run.cancel_requested_at is not None:
        if run.cancel_requested_at < run.created_at:
            raise ValueError("cancel_requested_at cannot precede created_at")
        if run.cancel_requested_at > run.updated_at:
            raise ValueError("cancel_requested_at cannot follow updated_at")
        if run.cancelled_at is not None and run.cancelled_at < run.cancel_requested_at:
            raise ValueError("cancelled_at cannot precede cancel_requested_at")


def _validate_budget_summary(
    *,
    total: BudgetLimits,
    reserved: BudgetLimits,
    consumed: BudgetLimits,
) -> None:
    for dimension in BudgetLimits.model_fields:
        if not (
            getattr(consumed, dimension)
            <= getattr(reserved, dimension)
            <= getattr(total, dimension)
        ):
            raise ValueError(
                f"budget dimension {dimension} must satisfy consumed <= reserved <= total"
            )


def transition_run(
    run: RunModel,
    target_status: RunStatus,
    *,
    expected_version: int,
    occurred_at: datetime,
) -> RunModel:
    occurred_at = _UTC_DATE_TIME_ADAPTER.validate_python(occurred_at)
    if expected_version != run.version:
        raise DelegationModelError(
            "run_version_conflict",
            f"expected run version {expected_version}, found {run.version}",
        )
    if run.status in _TERMINAL_STATUSES:
        raise DelegationModelError(
            "terminal_state_immutable",
            f"terminal run status {run.status} cannot transition",
        )
    if occurred_at < run.updated_at:
        raise DelegationModelError(
            "run_event_time_conflict",
            "run transition time cannot precede the current updated_at",
        )
    allowed = _PARENT_TRANSITIONS if isinstance(run, ParentRun) else _COMMON_TRANSITIONS
    if target_status not in allowed.get(run.status, frozenset()):
        raise DelegationModelError(
            "invalid_run_status_transition",
            f"cannot transition {type(run).__name__} from {run.status} to {target_status}",
        )
    updates: dict[str, Any] = {
        "status": target_status,
        "version": run.version + 1,
        "updated_at": occurred_at,
    }
    if target_status == "running" and run.started_at is None:
        updates["started_at"] = occurred_at
    if target_status in _TERMINAL_STATUSES:
        updates["completed_at"] = occurred_at
    if target_status == "cancelling" and isinstance(run, ParentRun):
        updates["cancel_requested_at"] = occurred_at
        updates["cancellation_version"] = run.cancellation_version + 1
    if target_status == "cancelled":
        updates["cancelled_at"] = occurred_at
    if isinstance(run, ChildRun) and target_status != "running":
        updates.update(
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            heartbeat_at=None,
        )
    payload = run.model_dump(mode="python")
    payload.update(updates)
    return type(run).model_validate(payload)


def validate_child_result_acceptance(
    run: ChildRun,
    envelope: ResultEnvelope,
    *,
    expected_version: int,
) -> None:
    if expected_version != run.version:
        raise DelegationModelError(
            "run_version_conflict",
            f"expected run version {expected_version}, found {run.version}",
        )
    if run.status in _TERMINAL_STATUSES or run.status == "cancelling":
        raise DelegationModelError(
            "late_child_result_rejected",
            f"child result cannot be accepted while run is {run.status}",
        )
    if run.status != "running":
        raise DelegationModelError(
            "child_result_not_acceptable",
            f"child result cannot be accepted while run is {run.status}",
        )
    if envelope.child_run_id != run.id or envelope.task_id != run.child_task_id:
        raise DelegationModelError(
            "child_result_identity_mismatch",
            "result envelope identifiers do not match the child run",
        )
