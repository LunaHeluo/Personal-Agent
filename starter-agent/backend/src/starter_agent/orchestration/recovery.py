from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Literal, Mapping

from pydantic import Field

from starter_agent.capabilities.models import canonical_json_sha256
from starter_agent.orchestration.budget import OrchestrationBudgetManager
from starter_agent.orchestration.models import (
    BudgetAmounts,
    BudgetSnapshot,
    OrchestrationModel,
    RecoveryAttempt,
    VerifyFailure,
    VerifyResult,
)


class RecoveryPolicy(OrchestrationModel):
    max_attempts: int = Field(default=2, ge=1, le=2)
    high_risk_action: Literal["human_review", "stop"] = "human_review"


class RecoveryTarget(OrchestrationModel):
    failure_id: str = Field(min_length=1, max_length=160)
    path: str = Field(min_length=1, max_length=200)
    fragment_ref: str = Field(min_length=1, max_length=500)
    evidence_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=256)


class RecoveryRequest(OrchestrationModel):
    recovery_id: str
    verify_id: str
    failure_ids: tuple[str, ...]
    targets: tuple[RecoveryTarget, ...]
    strategy: Literal[
        "field_patch", "citation_retrieval", "step_retry", "section_rewrite"
    ]
    budget_limit: BudgetAmounts
    deadline_at: datetime


class RecoveryPatch(OrchestrationModel):
    patch_ref: str = Field(min_length=1, max_length=500)
    output_ref: str = Field(min_length=1, max_length=500)
    target_paths: tuple[str, ...] = Field(min_length=1, max_length=256)
    usage: BudgetAmounts


class RecoveryDecision(OrchestrationModel):
    outcome: Literal["execute", "human_review", "stop"]
    reason_code: str
    request: RecoveryRequest | None = None
    attempt: RecoveryAttempt | None = None


RepairCallable = Callable[[RecoveryRequest], RecoveryPatch]


class BoundedRecovery:
    """Repair only named failures; never restarts Planner or the full Plan."""

    def __init__(
        self,
        *,
        policy: RecoveryPolicy | None = None,
        budget_manager: OrchestrationBudgetManager | None = None,
    ) -> None:
        self._policy = policy or RecoveryPolicy()
        self._budget_manager = budget_manager or OrchestrationBudgetManager()

    def propose(
        self,
        verify_result: VerifyResult,
        *,
        parent_run_id: str,
        revision_count: int,
        prior_attempts: tuple[RecoveryAttempt, ...],
        fragment_refs: Mapping[str, str],
        frozen_item_refs: tuple[str, ...],
        budget_snapshot: BudgetSnapshot,
        recovery_budget: BudgetAmounts,
        deadline_at: datetime,
        started_at: datetime,
        risk_level: str = "low",
    ) -> RecoveryDecision:
        if verify_result.passed:
            return RecoveryDecision(outcome="stop", reason_code="verification_already_passed")
        if risk_level in {"high", "critical"}:
            return RecoveryDecision(
                outcome=self._policy.high_risk_action,
                reason_code="recovery_risk_requires_review",
            )
        repairable = tuple(item for item in verify_result.failures if item.repairable)
        if len(repairable) != len(verify_result.failures):
            return RecoveryDecision(outcome="stop", reason_code="failure_not_repairable")
        if revision_count >= self._policy.max_attempts:
            return RecoveryDecision(outcome="stop", reason_code="recovery_limit_reached")
        fingerprint = self._fingerprint(repairable)
        if any(
            tuple(item.failure_ids) == tuple(failure.failure_id for failure in repairable)
            and item.status in {"succeeded", "failed"}
            for item in prior_attempts
        ):
            return RecoveryDecision(outcome="stop", reason_code="same_failure_repeated")
        permit = self._budget_manager.preflight(budget_snapshot, recovery_budget)
        if not permit.allowed:
            return RecoveryDecision(outcome="stop", reason_code="recovery_budget_insufficient")
        missing_fragments = tuple(
            item.failure_id for item in repairable if item.failure_id not in fragment_refs
        )
        if missing_fragments:
            return RecoveryDecision(outcome="stop", reason_code="recovery_fragment_missing")
        attempt_no = revision_count + 1
        recovery_id = f"recovery:{canonical_json_sha256({'verify': verify_result.verify_id, 'attempt': attempt_no, 'fingerprint': fingerprint})[:32]}"
        strategy = self._strategy(repairable)
        targets = tuple(
            RecoveryTarget(
                failure_id=item.failure_id,
                path=item.path,
                fragment_ref=fragment_refs[item.failure_id],
                evidence_refs=item.evidence_refs,
            )
            for item in repairable
        )
        request = RecoveryRequest(
            recovery_id=recovery_id,
            verify_id=verify_result.verify_id,
            failure_ids=tuple(item.failure_id for item in repairable),
            targets=targets,
            strategy=strategy,
            budget_limit=recovery_budget,
            deadline_at=deadline_at,
        )
        attempt = RecoveryAttempt(
            recovery_id=recovery_id,
            parent_run_id=parent_run_id,
            plan_id=verify_result.plan_id,
            step_id=verify_result.step_id,
            verify_id=verify_result.verify_id,
            attempt_no=attempt_no,
            failure_ids=request.failure_ids,
            frozen_item_refs=frozen_item_refs,
            strategy=strategy,
            input_refs=tuple(item.fragment_ref for item in targets),
            status="proposed",
            budget_before_id=budget_snapshot.budget_snapshot_id,
            started_at=started_at,
        )
        return RecoveryDecision(
            outcome="execute",
            reason_code="targeted_recovery_available",
            request=request,
            attempt=attempt,
        )

    def execute(
        self,
        decision: RecoveryDecision,
        *,
        repair: RepairCallable,
        budget_snapshot: BudgetSnapshot,
        completed_at: datetime,
        budget_snapshot_id: str,
    ) -> tuple[RecoveryAttempt, BudgetSnapshot]:
        if decision.outcome != "execute" or decision.request is None or decision.attempt is None:
            raise ValueError("recovery_not_executable")
        patch = repair(decision.request)
        allowed_paths = {item.path for item in decision.request.targets}
        if any(path in {"$", "*", "$.*"} or path not in allowed_paths for path in patch.target_paths):
            raise ValueError("recovery_patch_scope_violation")
        updated_budget = self._budget_manager.consume(
            budget_snapshot,
            operation_id=f"consume:{decision.request.recovery_id}",
            usage=patch.usage,
            snapshot_id=budget_snapshot_id,
            created_at=completed_at,
        )
        attempt = decision.attempt.model_copy(
            update={
                "patch_ref": patch.patch_ref,
                "output_ref": patch.output_ref,
                "status": "succeeded",
                "budget_after_id": updated_budget.budget_snapshot_id,
                "completed_at": completed_at,
            }
        )
        return attempt, updated_budget

    @staticmethod
    def _strategy(failures: tuple[VerifyFailure, ...]):
        if all(item.rule_id == "citation.complete" for item in failures):
            return "citation_retrieval"
        if all(item.path != "$" for item in failures):
            return "field_patch"
        return "section_rewrite"

    @staticmethod
    def _fingerprint(failures: tuple[VerifyFailure, ...]) -> str:
        return canonical_json_sha256(
            [(item.rule_id, item.path, item.actual_summary) for item in failures]
        )
