from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from starter_agent.capabilities.models import (
    BoundedJsonObject,
    Identifier,
    Sha256,
    UtcDateTime,
)


SchemaVersion = Annotated[str, Field(min_length=1, max_length=32)]
ShortText = Annotated[str, Field(min_length=1, max_length=200)]
LongText = Annotated[str, Field(min_length=1, max_length=4_000)]
Reference = Annotated[str, Field(min_length=1, max_length=500)]
NonNegativeInt = Annotated[int, Field(ge=0, le=2**63 - 1)]
PositiveInt = Annotated[int, Field(ge=1, le=2**63 - 1)]

RouteName = Literal[
    "direct",
    "workflow",
    "tool_loop",
    "plan_delegation",
    "human_review",
]
RiskLevel = Literal["low", "medium", "high", "critical"]
ExecutionStatus = Literal[
    "created",
    "running",
    "waiting",
    "partial",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
]
NodeName = Literal[
    "start",
    "load_state",
    "router",
    "planner",
    "plan_validator",
    "task_manager",
    "executor",
    "join",
    "merge",
    "verifier",
    "recovery",
    "human_review",
    "end",
    "stop",
    "cancelled",
    "interrupted",
]


class OrchestrationModel(BaseModel):
    """Strict, immutable and versionable orchestration contract."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class RouteFallback(OrchestrationModel):
    route: RouteName
    condition_code: ShortText
    user_prompt: str | None = Field(default=None, max_length=2_000)


class RouteDecision(OrchestrationModel):
    schema_version: SchemaVersion = "1"
    route_decision_id: Identifier
    run_id: Identifier | None = None
    session_id: Identifier
    turn_id: Identifier
    route: RouteName
    confidence: float = Field(ge=0, le=1)
    reason_code: ShortText
    reason_summary: str = Field(min_length=1, max_length=2_000)
    required_capabilities: tuple[Identifier, ...] = Field(default_factory=tuple, max_length=64)
    risk_level: RiskLevel
    missing_inputs: tuple[ShortText, ...] = Field(default_factory=tuple, max_length=64)
    matched_rules: tuple[Identifier, ...] = Field(default_factory=tuple, max_length=128)
    conflicting_rules: tuple[Identifier, ...] = Field(default_factory=tuple, max_length=128)
    fallback: RouteFallback
    capability_snapshot_revision: ShortText
    policy_revision: ShortText
    model_decision_id: Identifier | None = None
    status: Literal[
        "proposed", "accepted", "superseded", "clarification_required"
    ] = "accepted"
    supersedes: Identifier | None = None
    created_at: UtcDateTime


class BudgetAmounts(OrchestrationModel):
    steps: NonNegativeInt = 0
    tokens: NonNegativeInt = 0
    cost_microunits: NonNegativeInt = 0
    wall_clock_ms: NonNegativeInt = 0
    tool_calls: NonNegativeInt = 0
    model_calls: NonNegativeInt = 0


class DoneWhenRule(OrchestrationModel):
    rule_id: Identifier
    type: Literal["schema", "business_rule", "source", "citation", "rubric"]
    expected: BoundedJsonObject = Field(default_factory=dict)


class PlanStep(OrchestrationModel):
    schema_version: SchemaVersion = "1"
    step_id: Identifier
    plan_id: Identifier
    ordinal: PositiveInt
    status: Literal[
        "blocked",
        "ready",
        "queued",
        "running",
        "waiting",
        "succeeded",
        "partial",
        "failed",
        "cancelled",
        "timed_out",
        "skipped",
        "interrupted",
    ] = "blocked"
    goal: LongText
    input_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=256)
    capabilities: tuple[Identifier, ...] = Field(default_factory=tuple, max_length=64)
    done_when: tuple[DoneWhenRule, ...] = Field(default_factory=tuple, max_length=64)
    risk: RiskLevel
    budget_limit: BudgetAmounts
    deadline_at: UtcDateTime
    depends_on: tuple[Identifier, ...] = Field(default_factory=tuple, max_length=256)
    execution: Literal["local", "workflow", "tool_loop", "child"]
    workflow_id: Identifier | None = None
    specialist_id: Identifier | None = None
    output_contract_ref: Reference
    result_ref: Reference | None = None
    required: bool = True
    failure_behavior: Literal["fail_parent", "allow_partial", "wait_for_user"] = (
        "fail_parent"
    )
    shared_resource_keys: tuple[ShortText, ...] = Field(default_factory=tuple, max_length=128)
    read_resource_keys: tuple[ShortText, ...] = Field(default_factory=tuple, max_length=128)
    write_resource_keys: tuple[ShortText, ...] = Field(default_factory=tuple, max_length=128)
    rate_limit_keys: tuple[ShortText, ...] = Field(default_factory=tuple, max_length=128)
    parallel_candidate: bool = False
    parallel_decision_reason: str | None = Field(default=None, max_length=2_000)
    attempt_count: NonNegativeInt = 0
    recovery_count: NonNegativeInt = 0

    @model_validator(mode="after")
    def validate_execution_reference(self) -> "PlanStep":
        if self.execution == "workflow" and self.workflow_id is None:
            raise ValueError("workflow execution requires workflow_id")
        if self.execution == "child" and self.specialist_id is None:
            raise ValueError("child execution requires specialist_id")
        if self.step_id in self.depends_on:
            raise ValueError("a plan step cannot depend on itself")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("depends_on must be unique")
        return self


class PlanEdge(OrchestrationModel):
    source_step_id: Identifier
    target_step_id: Identifier
    reason: Literal["input", "write_conflict", "explicit", "risk_order"]


class Plan(OrchestrationModel):
    schema_version: SchemaVersion = "1"
    plan_id: Identifier
    parent_run_id: Identifier
    version: NonNegativeInt = 0
    status: Literal[
        "draft",
        "validating",
        "valid",
        "invalid",
        "executing",
        "waiting",
        "completed",
        "partial",
        "failed",
        "cancelled",
        "superseded",
    ] = "draft"
    goal: LongText
    assumptions: tuple[ShortText, ...] = Field(default_factory=tuple, max_length=128)
    input_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=256)
    steps: tuple[PlanStep, ...] = Field(min_length=1, max_length=1_000)
    edges: tuple[PlanEdge, ...] = Field(default_factory=tuple, max_length=10_000)
    join_policy: Literal[
        "all_required", "partial_allowed", "first_success", "deadline_reached"
    ] = "all_required"
    minimum_success: NonNegativeInt | None = None
    budget_total: BudgetAmounts
    deadline_at: UtcDateTime
    validation_result_id: Identifier | None = None
    revision_count: NonNegativeInt = 0
    created_at: UtcDateTime
    updated_at: UtcDateTime

    @model_validator(mode="after")
    def validate_structure(self) -> "Plan":
        step_ids = tuple(step.step_id for step in self.steps)
        if len(set(step_ids)) != len(step_ids):
            raise ValueError("plan step ids must be unique")
        if any(step.plan_id != self.plan_id for step in self.steps):
            raise ValueError("plan step plan_id must match plan")
        known = set(step_ids)
        for edge in self.edges:
            if edge.source_step_id not in known or edge.target_step_id not in known:
                raise ValueError("plan edge references an unknown step")
        if self.minimum_success is not None and self.minimum_success > len(self.steps):
            raise ValueError("minimum_success cannot exceed step count")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        return self


class TaskProgress(OrchestrationModel):
    completed: NonNegativeInt = 0
    total: NonNegativeInt = 0

    @model_validator(mode="after")
    def validate_completed(self) -> "TaskProgress":
        if self.completed > self.total:
            raise ValueError("completed progress cannot exceed total")
        return self


class BackgroundTask(OrchestrationModel):
    schema_version: SchemaVersion = "1"
    task_id: Identifier
    parent_run_id: Identifier
    session_id: Identifier
    origin_turn_id: Identifier
    status: Literal[
        "queued",
        "running",
        "waiting",
        "partial",
        "completed",
        "failed",
        "cancelled",
        "interrupted",
    ]
    internal_status: ShortText
    reason_code: ShortText | None = None
    phase: ShortText
    plan_id: Identifier | None = None
    current_step_id: Identifier | None = None
    progress: TaskProgress = Field(default_factory=TaskProgress)
    budget_snapshot_id: Identifier
    pending_action_id: Identifier | None = None
    created_at: UtcDateTime
    started_at: UtcDateTime | None = None
    updated_at: UtcDateTime
    completed_at: UtcDateTime | None = None
    version: NonNegativeInt = 0

    @model_validator(mode="after")
    def validate_timestamps(self) -> "BackgroundTask":
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("started_at cannot precede created_at")
        terminal = {"partial", "completed", "failed", "cancelled", "interrupted"}
        if self.status in terminal and self.completed_at is None:
            raise ValueError("terminal background task requires completed_at")
        if self.completed_at is not None and self.completed_at < self.created_at:
            raise ValueError("completed_at cannot precede created_at")
        return self


class ChildRunSnapshot(OrchestrationModel):
    child_run_id: Identifier
    child_task_id: Identifier
    parent_run_id: Identifier
    plan_step_id: Identifier | None = None
    attempt: PositiveInt
    status: ShortText
    phase: ShortText
    result_envelope_ref: Reference | None = None
    stop_reason_code: ShortText | None = None
    last_task_event_seq: NonNegativeInt = 0


class TaskEvent(OrchestrationModel):
    schema_version: SchemaVersion = "1"
    task_event_id: Identifier
    event_seq: PositiveInt
    event_type: Literal[
        "child_started",
        "child_progress",
        "child_completed",
        "child_failed",
        "child_cancelled",
        "child_timed_out",
        "route",
        "plan",
        "validation",
        "join",
        "verify",
        "recovery",
        "budget",
        "approval",
        "cancel",
        "terminal",
    ]
    task_id: Identifier
    parent_run_id: Identifier
    child_run_id: Identifier | None = None
    plan_id: Identifier | None = None
    step_id: Identifier | None = None
    attempt: PositiveInt = 1
    status: ShortText
    occurred_at: UtcDateTime
    payload_summary: BoundedJsonObject = Field(default_factory=dict)
    artifact_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=256)
    budget_snapshot_id: Identifier | None = None
    payload_hash: Sha256
    source_event_id: Identifier | None = None
    late_ignored: bool = False


class JoinDecision(OrchestrationModel):
    schema_version: SchemaVersion = "1"
    join_decision_id: Identifier
    parent_run_id: Identifier
    plan_id: Identifier
    policy: Literal[
        "all_required", "partial_allowed", "first_success", "deadline_reached"
    ]
    required_task_ids: tuple[Identifier, ...] = Field(default_factory=tuple, max_length=1_000)
    optional_task_ids: tuple[Identifier, ...] = Field(default_factory=tuple, max_length=1_000)
    accepted: tuple[Identifier, ...] = Field(default_factory=tuple, max_length=1_000)
    partial: tuple[Identifier, ...] = Field(default_factory=tuple, max_length=1_000)
    failed: tuple[Identifier, ...] = Field(default_factory=tuple, max_length=1_000)
    timed_out: tuple[Identifier, ...] = Field(default_factory=tuple, max_length=1_000)
    cancelled: tuple[Identifier, ...] = Field(default_factory=tuple, max_length=1_000)
    missing: tuple[Identifier, ...] = Field(default_factory=tuple, max_length=1_000)
    late_ignored: tuple[Identifier, ...] = Field(default_factory=tuple, max_length=1_000)
    minimum_success: NonNegativeInt | None = None
    deadline_at: UtcDateTime
    satisfied: bool
    outcome: Literal["wait", "merge", "human_review", "fail"]
    reason_code: ShortText
    merge_input_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=1_000)
    decided_at: UtcDateTime
    state_version: NonNegativeInt


class ValidationIssue(OrchestrationModel):
    issue_id: Identifier
    code: ShortText
    path: ShortText
    severity: Literal["info", "warning", "error", "critical"]
    expected: BoundedJsonObject = Field(default_factory=dict)
    actual_summary: BoundedJsonObject = Field(default_factory=dict)
    repairable: bool
    suggested_action: ShortText
    related_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=256)


class ValidationResult(OrchestrationModel):
    schema_version: SchemaVersion = "1"
    validation_result_id: Identifier
    plan_id: Identifier
    plan_version: NonNegativeInt
    valid: bool
    issues: tuple[ValidationIssue, ...] = Field(default_factory=tuple, max_length=1_000)
    capability_snapshot_revision: ShortText
    policy_revision: ShortText
    budget_snapshot_id: Identifier
    validated_at: UtcDateTime
    decision: Literal["execute", "revise", "human_review", "stop"]

    @model_validator(mode="after")
    def validate_decision(self) -> "ValidationResult":
        if self.valid != (self.decision == "execute"):
            raise ValueError("only a valid plan can have execute decision")
        if not self.valid and not self.issues:
            raise ValueError("invalid validation result requires issues")
        return self


class VerifyFailure(OrchestrationModel):
    failure_id: Identifier
    scope: ShortText
    path: ShortText
    rule_id: Identifier
    expected: BoundedJsonObject = Field(default_factory=dict)
    actual_summary: BoundedJsonObject = Field(default_factory=dict)
    severity: Literal["info", "warning", "error", "critical"]
    repairable: bool
    evidence_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=256)


class JudgeResultSummary(OrchestrationModel):
    passed: bool
    rubric_scores: BoundedJsonObject = Field(default_factory=dict)
    reason_summary: str = Field(max_length=2_000)


class VerifyResult(OrchestrationModel):
    schema_version: SchemaVersion = "1"
    verify_id: Identifier
    parent_run_id: Identifier
    plan_id: Identifier | None = None
    step_id: Identifier | None = None
    output_ref: Reference
    passed: bool
    verified_items: tuple[Identifier, ...] = Field(default_factory=tuple, max_length=1_000)
    failures: tuple[VerifyFailure, ...] = Field(default_factory=tuple, max_length=1_000)
    deterministic_result: BoundedJsonObject
    judge_result: JudgeResultSummary | None = None
    judge_model_decision_id: Identifier | None = None
    decision: Literal["end", "recovery", "human_review", "partial", "stop"]
    budget_snapshot_id: Identifier
    created_at: UtcDateTime

    @model_validator(mode="after")
    def validate_outcome(self) -> "VerifyResult":
        if self.passed and self.failures:
            raise ValueError("passed verification cannot contain failures")
        if not self.passed and not self.failures:
            raise ValueError("failed verification requires failures")
        if self.passed and self.decision != "end":
            raise ValueError("passed verification must end")
        return self


class RecoveryAttempt(OrchestrationModel):
    schema_version: SchemaVersion = "1"
    recovery_id: Identifier
    parent_run_id: Identifier
    plan_id: Identifier | None = None
    step_id: Identifier | None = None
    verify_id: Identifier
    attempt_no: Annotated[int, Field(ge=1, le=2)]
    failure_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=256)
    frozen_item_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=1_000)
    strategy: Literal[
        "field_patch", "citation_retrieval", "step_retry", "section_rewrite"
    ]
    input_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=256)
    patch_ref: Reference | None = None
    output_ref: Reference | None = None
    status: Literal[
        "proposed",
        "running",
        "succeeded",
        "failed",
        "budget_exhausted",
        "cancelled",
    ] = "proposed"
    budget_before_id: Identifier
    budget_after_id: Identifier | None = None
    model_decision_id: Identifier | None = None
    started_at: UtcDateTime
    completed_at: UtcDateTime | None = None
    error_code: ShortText | None = None


class BudgetSnapshot(OrchestrationModel):
    schema_version: SchemaVersion = "1"
    budget_snapshot_id: Identifier
    parent_run_id: Identifier
    child_run_id: Identifier | None = None
    step_id: Identifier | None = None
    version: NonNegativeInt = 0
    phase: Literal["preflight", "reserved", "consumed", "settled", "stopped"]
    limit: BudgetAmounts
    reserved: BudgetAmounts
    consumed: BudgetAmounts
    released: BudgetAmounts
    remaining: BudgetAmounts
    overage: BudgetAmounts
    cost_status: Literal["actual", "estimated", "unknown"]
    price_version: ShortText | None = None
    usage_source: ShortText | None = None
    stop_dimension: Literal[
        "steps",
        "tokens",
        "cost_microunits",
        "wall_clock_ms",
        "tool_calls",
        "model_calls",
    ] | None = None
    applied_operation_ids: tuple[Identifier, ...] = Field(
        default_factory=tuple,
        max_length=4_096,
    )
    created_at: UtcDateTime


class ModelRequirements(OrchestrationModel):
    capabilities: tuple[Identifier, ...] = Field(default_factory=tuple, max_length=64)
    complexity: Literal["trivial", "bounded", "complex"]
    latency_class: Literal["interactive", "standard", "background"]
    context_tokens: NonNegativeInt
    risk_policy: ShortText


class ModelCandidate(OrchestrationModel):
    provider: Identifier
    model: ShortText
    capabilities: tuple[Identifier, ...] = Field(default_factory=tuple, max_length=64)
    cost_estimate_microunits: NonNegativeInt | None = None
    latency_class: Literal["interactive", "standard", "background"]
    health: Literal["healthy", "degraded", "unavailable", "unknown"]


class ModelDecision(OrchestrationModel):
    schema_version: SchemaVersion = "1"
    model_decision_id: Identifier
    parent_run_id: Identifier | None = None
    step_id: Identifier | None = None
    purpose: Literal["router", "planner", "executor", "judge", "recovery"]
    requirements: ModelRequirements
    candidates: tuple[ModelCandidate, ...] = Field(default_factory=tuple, max_length=128)
    selected_provider: Identifier | None = None
    selected_model: ShortText | None = None
    reason_code: ShortText
    reason_summary: str = Field(min_length=1, max_length=2_000)
    fallback_chain: tuple[ShortText, ...] = Field(default_factory=tuple, max_length=32)
    config_revision: ShortText
    pricing_version: ShortText | None = None
    budget_snapshot_id: Identifier | None = None
    status: Literal["selected", "fallback", "unavailable"]
    created_at: UtcDateTime

    @model_validator(mode="after")
    def validate_selection(self) -> "ModelDecision":
        selected = (self.selected_provider, self.selected_model)
        if (selected[0] is None) != (selected[1] is None):
            raise ValueError("selected provider and model must be set together")
        if self.status == "unavailable" and selected != (None, None):
            raise ValueError("unavailable decision cannot select a model")
        if self.status != "unavailable" and selected == (None, None):
            raise ValueError("selected/fallback decision requires a model")
        if selected != (None, None) and not any(
            candidate.provider == selected[0] and candidate.model == selected[1]
            for candidate in self.candidates
        ):
            raise ValueError("selected model must be one of the candidates")
        return self


class PendingAction(OrchestrationModel):
    schema_version: SchemaVersion = "1"
    pending_action_id: Identifier
    parent_run_id: Identifier
    step_id: Identifier | None = None
    action_type: ShortText
    tool_name: Identifier | None = None
    target_summary: ShortText
    arguments_hash: Sha256
    content_diff_ref: Reference | None = None
    attachment_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=128)
    risk_level: RiskLevel
    irreversible: bool
    impact_summary: tuple[ShortText, ...] = Field(default_factory=tuple, max_length=64)
    confirmation_id: Identifier | None = None
    approval_id: Identifier | None = None
    status: Literal[
        "pending",
        "approved",
        "rejected",
        "expired",
        "invalidated",
        "consumed",
        "cancelled",
    ] = "pending"
    principal: ShortText
    expires_at: UtcDateTime
    policy_revision: ShortText
    gate_decision_id: Identifier
    created_at: UtcDateTime
    decided_at: UtcDateTime | None = None
    consumed_at: UtcDateTime | None = None

    @model_validator(mode="after")
    def validate_identity(self) -> "PendingAction":
        if self.confirmation_id is not None and self.approval_id is not None:
            raise ValueError("pending action has one approval authority")
        if self.expires_at <= self.created_at:
            raise ValueError("pending action expiry must follow creation")
        return self


class RevisionCount(OrchestrationModel):
    plan: NonNegativeInt = 0
    recovery: Annotated[int, Field(ge=0, le=2)] = 0
    infrastructure: NonNegativeInt = 0


class ExecutionState(OrchestrationModel):
    schema_version: SchemaVersion = "1"
    run_id: Identifier
    parent_run_id: Identifier | None = None
    session_id: Identifier
    turn_id: Identifier
    goal: LongText
    execution_status: ExecutionStatus = "created"
    current_node: NodeName = "start"
    route: RouteDecision | None = None
    plan: Plan | None = None
    current_step: Identifier | None = None
    outputs: BoundedJsonObject = Field(default_factory=dict)
    artifact_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=1_000)
    budget: BudgetSnapshot | None = None
    pending_action: PendingAction | None = None
    revision_count: RevisionCount = Field(default_factory=RevisionCount)
    background_task: BackgroundTask | None = None
    child_runs: tuple[ChildRunSnapshot, ...] = Field(default_factory=tuple, max_length=1_000)
    latest_join_decision: JoinDecision | None = None
    latest_verify_result: VerifyResult | None = None
    model_decisions: tuple[ModelDecision, ...] = Field(default_factory=tuple, max_length=256)
    stop_reason: ShortText | None = None
    state_version: NonNegativeInt = 0
    updated_at: UtcDateTime | None = None

    @model_validator(mode="after")
    def validate_route_boundaries(self) -> "ExecutionState":
        route = None if self.route is None else self.route.route
        if route == "direct":
            if self.plan is not None:
                raise ValueError("direct route cannot own a plan")
            if self.background_task is not None or self.child_runs:
                raise ValueError("direct route cannot own background or child runs")
        if self.current_node in {"planner", "plan_validator", "task_manager", "join", "merge"}:
            if route != "plan_delegation":
                raise ValueError("plan/delegation nodes require plan_delegation route")
        if self.current_node == "human_review" and self.execution_status == "waiting":
            if self.pending_action is None:
                raise ValueError("waiting human review requires a pending action")
        terminal_nodes = {"end", "stop", "cancelled", "interrupted"}
        terminal_statuses = {"partial", "completed", "failed", "cancelled", "interrupted"}
        if self.current_node in terminal_nodes and self.execution_status not in terminal_statuses:
            raise ValueError("terminal node requires terminal execution status")
        if self.current_step is not None and self.plan is None:
            raise ValueError("current_step requires a plan")
        return self
