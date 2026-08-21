from __future__ import annotations

from collections import Counter
from typing import Literal, Mapping

from pydantic import Field

from starter_agent.orchestration.budget import OrchestrationBudgetManager
from starter_agent.orchestration.models import (
    BudgetAmounts,
    BudgetSnapshot,
    Identifier,
    OrchestrationModel,
    Plan,
    PlanStep,
)


class SchedulerLimits(OrchestrationModel):
    max_parallel: int = Field(default=1, ge=1, le=256)
    available_slots: int = Field(default=1, ge=0, le=256)
    rate_limit_capacity: Mapping[str, int] = Field(default_factory=dict)


class DAGSchedulingDecision(OrchestrationModel):
    mode: Literal["parallel", "serial", "waiting", "complete"]
    selected_step_ids: tuple[Identifier, ...] = ()
    deferred_step_ids: tuple[Identifier, ...] = ()
    reason_code: str
    reasons: Mapping[str, str] = Field(default_factory=dict)


class DAGScheduleError(ValueError):
    pass


class DAGScheduler:
    """Select a deterministic ready set; execution remains an Executor concern."""

    _SUCCESS = frozenset({"succeeded", "partial", "skipped"})
    _TERMINAL = frozenset(
        {"succeeded", "partial", "failed", "cancelled", "timed_out", "skipped", "interrupted"}
    )

    def __init__(
        self,
        *,
        budget_snapshot: BudgetSnapshot,
        limits: SchedulerLimits,
        budget_manager: OrchestrationBudgetManager | None = None,
    ) -> None:
        self._budget = budget_snapshot
        self._limits = limits
        self._budget_manager = budget_manager or OrchestrationBudgetManager()

    def decide(self, plan: Plan) -> DAGSchedulingDecision:
        self._assert_acyclic(plan)
        ordered = tuple(sorted(plan.steps, key=lambda item: (item.ordinal, item.step_id)))
        by_id = {step.step_id: step for step in ordered}
        ready = tuple(
            step
            for step in ordered
            if step.status not in self._TERMINAL
            and step.status != "running"
            and all(by_id[dependency].status in self._SUCCESS for dependency in step.depends_on)
        )
        deferred = tuple(
            step.step_id
            for step in ordered
            if step.status not in self._TERMINAL and step not in ready
        )
        if not ready:
            if all(step.status in self._TERMINAL for step in ordered):
                return DAGSchedulingDecision(mode="complete", reason_code="all_steps_terminal")
            return DAGSchedulingDecision(
                mode="waiting",
                deferred_step_ids=deferred,
                reason_code="dependencies_pending",
            )
        if self._limits.available_slots == 0:
            return DAGSchedulingDecision(
                mode="waiting",
                deferred_step_ids=tuple(step.step_id for step in ready) + deferred,
                reason_code="backpressure",
            )

        capacity = min(self._limits.max_parallel, self._limits.available_slots)
        candidates = ready[:capacity]
        remaining = tuple(step.step_id for step in ready[capacity:]) + deferred
        if len(candidates) < 2:
            return self._serial(candidates[0], remaining, "single_ready_step")

        ineligible = next((step for step in candidates if not step.parallel_candidate), None)
        if ineligible is not None:
            return self._serial(
                candidates[0],
                tuple(step.step_id for step in candidates[1:]) + remaining,
                "parallel_not_declared",
                {ineligible.step_id: "parallel_not_declared"},
            )
        missing_envelope = next(
            (
                step
                for step in candidates
                if not step.output_contract_ref.startswith("result-envelope:")
            ),
            None,
        )
        if missing_envelope is not None:
            return self._serial(
                candidates[0],
                tuple(step.step_id for step in candidates[1:]) + remaining,
                "result_envelope_required",
                {missing_envelope.step_id: "result_envelope_required"},
            )

        conflict_reasons = self._conflicts(candidates)
        if conflict_reasons:
            return self._serial(
                candidates[0],
                tuple(step.step_id for step in candidates[1:]) + remaining,
                "shared_write_conflict",
                conflict_reasons,
            )

        requested = self._sum_budget(candidates)
        if not self._budget_manager.preflight(self._budget, requested).allowed:
            return self._serial(
                candidates[0],
                tuple(step.step_id for step in candidates[1:]) + remaining,
                "parallel_budget_insufficient",
            )

        rate_usage = Counter(key for step in candidates for key in step.rate_limit_keys)
        exceeded = tuple(
            key
            for key, count in rate_usage.items()
            if count > self._limits.rate_limit_capacity.get(key, count)
        )
        if exceeded:
            return self._serial(
                candidates[0],
                tuple(step.step_id for step in candidates[1:]) + remaining,
                "rate_limit_serialized",
                {step.step_id: "rate_limit_serialized" for step in candidates[1:]},
            )
        return DAGSchedulingDecision(
            mode="parallel",
            selected_step_ids=tuple(step.step_id for step in candidates),
            deferred_step_ids=remaining,
            reason_code="parallel_eligible",
        )

    @staticmethod
    def _serial(
        first: PlanStep,
        deferred: tuple[str, ...],
        reason_code: str,
        reasons: Mapping[str, str] | None = None,
    ) -> DAGSchedulingDecision:
        return DAGSchedulingDecision(
            mode="serial",
            selected_step_ids=(first.step_id,),
            deferred_step_ids=deferred,
            reason_code=reason_code,
            reasons=reasons or {},
        )

    @staticmethod
    def _conflicts(steps: tuple[PlanStep, ...]) -> dict[str, str]:
        reasons: dict[str, str] = {}
        for index, left in enumerate(steps):
            left_writes = set(left.write_resource_keys) | set(left.shared_resource_keys)
            for right in steps[index + 1 :]:
                right_writes = set(right.write_resource_keys) | set(right.shared_resource_keys)
                right_reads = set(right.read_resource_keys)
                left_reads = set(left.read_resource_keys)
                if left_writes & (right_writes | right_reads) or right_writes & left_reads:
                    reasons[right.step_id] = "shared_write_conflict"
        return reasons

    @staticmethod
    def _sum_budget(steps: tuple[PlanStep, ...]) -> BudgetAmounts:
        return BudgetAmounts(
            **{
                dimension: sum(
                    getattr(step.budget_limit, dimension) for step in steps
                )
                for dimension in BudgetAmounts.model_fields
            }
        )

    @staticmethod
    def _assert_acyclic(plan: Plan) -> None:
        dependencies = {step.step_id: set(step.depends_on) for step in plan.steps}
        unknown = {
            dependency
            for values in dependencies.values()
            for dependency in values
            if dependency not in dependencies
        }
        if unknown:
            raise DAGScheduleError(f"unknown_dependencies:{','.join(sorted(unknown))}")
        pending = {key: set(value) for key, value in dependencies.items()}
        while pending:
            ready = {key for key, value in pending.items() if not value}
            if not ready:
                raise DAGScheduleError("plan_cycle")
            pending = {
                key: value - ready for key, value in pending.items() if key not in ready
            }
