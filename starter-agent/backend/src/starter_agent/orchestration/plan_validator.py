from __future__ import annotations

from datetime import datetime

from pydantic import Field

from starter_agent.orchestration.models import (
    BudgetAmounts,
    BudgetSnapshot,
    OrchestrationModel,
    Plan,
    ValidationIssue,
    ValidationResult,
)


class PlanValidationContext(OrchestrationModel):
    capability_snapshot_revision: str = Field(min_length=1, max_length=200)
    policy_revision: str = Field(min_length=1, max_length=200)
    enabled_capabilities: tuple[str, ...] = Field(default_factory=tuple, max_length=1_000)
    authorized_capabilities: tuple[str, ...] = Field(default_factory=tuple, max_length=1_000)
    healthy_capabilities: tuple[str, ...] = Field(default_factory=tuple, max_length=1_000)
    approved_step_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=1_000)
    budget_snapshot: BudgetSnapshot
    run_deadline_at: datetime
    result_envelope_contract_prefix: str = Field(
        default="result-envelope:", min_length=1, max_length=100
    )


class PlanValidator:
    """Deterministic, fail-closed validation before any Plan step executes."""

    def validate(
        self,
        plan: Plan,
        *,
        context: PlanValidationContext,
        validation_result_id: str,
        validated_at: datetime,
    ) -> ValidationResult:
        issues: list[ValidationIssue] = []

        def add(
            code: str,
            path: str,
            *,
            repairable: bool,
            severity: str = "error",
            expected: dict | None = None,
            actual: dict | None = None,
            action: str = "revise_plan",
        ) -> None:
            issues.append(
                ValidationIssue(
                    issue_id=f"issue:{len(issues) + 1}:{code}",
                    code=code,
                    path=path,
                    severity=severity,
                    expected=expected or {},
                    actual_summary=actual or {},
                    repairable=repairable,
                    suggested_action=action,
                )
            )

        step_by_id = {step.step_id: step for step in plan.steps}
        for step in plan.steps:
            if not step.done_when:
                add(
                    "done_when_missing",
                    f"steps.{step.step_id}.done_when",
                    repairable=True,
                    expected={"minimum_rules": 1},
                    actual={"count": 0},
                )
            for dependency in step.depends_on:
                if dependency not in step_by_id:
                    add(
                        "dependency_missing",
                        f"steps.{step.step_id}.depends_on",
                        repairable=True,
                        expected={"dependency_exists": True},
                        actual={"dependency": dependency},
                    )
            if step.deadline_at > plan.deadline_at:
                add(
                    "step_deadline_exceeds_plan",
                    f"steps.{step.step_id}.deadline_at",
                    repairable=True,
                    expected={"at_or_before_plan_deadline": True},
                )
            for capability in step.capabilities:
                if capability not in context.enabled_capabilities:
                    add(
                        "capability_disabled",
                        f"steps.{step.step_id}.capabilities",
                        repairable=False,
                        expected={"enabled": True},
                        actual={"capability": capability},
                        action="enable_capability_or_stop",
                    )
                elif capability not in context.authorized_capabilities:
                    add(
                        "capability_unauthorized",
                        f"steps.{step.step_id}.capabilities",
                        repairable=False,
                        severity="critical",
                        actual={"capability": capability},
                        action="request_authorization_or_stop",
                    )
                elif capability not in context.healthy_capabilities:
                    add(
                        "capability_unhealthy",
                        f"steps.{step.step_id}.capabilities",
                        repairable=False,
                        actual={"capability": capability},
                        action="wait_or_stop",
                    )
            if step.risk in {"high", "critical"} and step.step_id not in context.approved_step_ids:
                add(
                    "approval_required",
                    f"steps.{step.step_id}.risk",
                    repairable=False,
                    severity="critical",
                    expected={"pending_action_or_approval": True},
                    actual={"risk": step.risk},
                    action="human_review",
                )
            if (
                step.execution == "child"
                and not step.output_contract_ref.startswith(
                    context.result_envelope_contract_prefix
                )
            ):
                add(
                    "child_result_envelope_required",
                    f"steps.{step.step_id}.output_contract_ref",
                    repairable=True,
                    expected={
                        "prefix": context.result_envelope_contract_prefix
                    },
                )

        if _has_cycle(plan):
            add(
                "plan_cycle",
                "steps.depends_on",
                repairable=False,
                severity="critical",
                expected={"acyclic": True},
                action="stop_until_goal_or_dependencies_change",
            )

        if plan.deadline_at > context.run_deadline_at:
            add(
                "plan_deadline_exceeds_run",
                "deadline_at",
                repairable=False,
                expected={"at_or_before_run_deadline": True},
                action="reduce_plan_or_extend_deadline",
            )

        allocated = _sum(step.budget_limit for step in plan.steps)
        for dimension in BudgetAmounts.model_fields:
            step_total = getattr(allocated, dimension)
            declared = getattr(plan.budget_total, dimension)
            remaining = getattr(context.budget_snapshot.remaining, dimension)
            if step_total > declared or declared > remaining:
                add(
                    "budget_exceeded",
                    f"budget_total.{dimension}",
                    repairable=False,
                    severity="critical",
                    expected={"remaining": remaining, "declared": declared},
                    actual={"step_total": step_total},
                    action="reduce_scope_or_increase_budget",
                )

        if not issues:
            decision = "execute"
        elif any(issue.code == "approval_required" for issue in issues):
            decision = "human_review"
        elif all(issue.repairable for issue in issues):
            decision = "revise"
        else:
            decision = "stop"
        return ValidationResult(
            validation_result_id=validation_result_id,
            plan_id=plan.plan_id,
            plan_version=plan.version,
            valid=not issues,
            issues=tuple(issues),
            capability_snapshot_revision=context.capability_snapshot_revision,
            policy_revision=context.policy_revision,
            budget_snapshot_id=context.budget_snapshot.budget_snapshot_id,
            validated_at=validated_at,
            decision=decision,
        )


def _has_cycle(plan: Plan) -> bool:
    known = {step.step_id for step in plan.steps}
    indegree = {step.step_id: 0 for step in plan.steps}
    followers: dict[str, list[str]] = {step.step_id: [] for step in plan.steps}
    for step in plan.steps:
        for dependency in step.depends_on:
            if dependency not in known:
                continue
            indegree[step.step_id] += 1
            followers[dependency].append(step.step_id)
    ready = [step_id for step_id, count in indegree.items() if count == 0]
    visited = 0
    while ready:
        current = ready.pop()
        visited += 1
        for follower in followers[current]:
            indegree[follower] -= 1
            if indegree[follower] == 0:
                ready.append(follower)
    return visited != len(plan.steps)


def _sum(values) -> BudgetAmounts:
    totals = {dimension: 0 for dimension in BudgetAmounts.model_fields}
    for value in values:
        for dimension in totals:
            totals[dimension] += getattr(value, dimension)
    return BudgetAmounts(**totals)

