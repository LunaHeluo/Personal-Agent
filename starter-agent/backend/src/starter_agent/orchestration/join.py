from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from starter_agent.capabilities.models import canonical_json_sha256
from starter_agent.orchestration.models import JoinDecision, OrchestrationModel


class JoinPolicyConfig(OrchestrationModel):
    parent_run_id: str = Field(min_length=1, max_length=160)
    plan_id: str = Field(min_length=1, max_length=160)
    policy: Literal[
        "all_required", "partial_allowed", "first_success", "deadline_reached"
    ]
    expected_child_run_ids: tuple[str, ...] = Field(min_length=1, max_length=1_000)
    required_child_run_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=1_000)
    minimum_success: int | None = Field(default=None, ge=1, le=1_000)
    accept_partial_results: bool = True
    unsatisfied_action: Literal["fail", "human_review"] = "fail"
    deadline_at: datetime


class JoinChild(OrchestrationModel):
    child_run_id: str = Field(min_length=1, max_length=160)
    step_id: str = Field(min_length=1, max_length=160)
    status: Literal[
        "created",
        "queued",
        "running",
        "waiting",
        "succeeded",
        "partial",
        "failed",
        "timed_out",
        "cancelled",
        "interrupted",
    ]
    result_envelope_ref: str | None = Field(default=None, max_length=500)


class MergeManifestItem(OrchestrationModel):
    child_run_id: str
    step_id: str
    result_envelope_ref: str
    result_status: Literal["succeeded", "partial"]


class MergeManifest(OrchestrationModel):
    parent_run_id: str
    join_decision_id: str
    inputs: tuple[MergeManifestItem, ...]
    failed: tuple[str, ...] = ()
    timed_out: tuple[str, ...] = ()
    cancelled: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()


class JoinEvaluation(OrchestrationModel):
    decision: JoinDecision
    merge_manifest: MergeManifest | None = None
    cancel_child_run_ids: tuple[str, ...] = ()
    next_node: Literal["wait", "merge", "verify", "human_review", "stop"]


class JoinEvaluator:
    _PENDING = frozenset({"created", "queued", "running", "waiting"})

    def evaluate(
        self,
        config: JoinPolicyConfig,
        children: tuple[JoinChild, ...],
        *,
        now: datetime,
        state_version: int,
        previous_decision: JoinDecision | None = None,
    ) -> JoinEvaluation:
        if previous_decision is not None and previous_decision.outcome != "wait":
            return self._from_committed(previous_decision, children)
        by_id = {item.child_run_id: item for item in children}
        expected = config.expected_child_run_ids
        observed = tuple(by_id[item] for item in expected if item in by_id)
        succeeded = tuple(
            item.child_run_id
            for item in observed
            if item.status == "succeeded" and item.result_envelope_ref is not None
        )
        partial = tuple(
            item.child_run_id
            for item in observed
            if item.status == "partial" and item.result_envelope_ref is not None
        )
        failed = tuple(item.child_run_id for item in observed if item.status in {"failed", "interrupted"})
        timed_out = tuple(item.child_run_id for item in observed if item.status == "timed_out")
        cancelled = tuple(item.child_run_id for item in observed if item.status == "cancelled")
        absent = tuple(item for item in expected if item not in by_id)
        pending = tuple(item.child_run_id for item in observed if item.status in self._PENDING)
        usable = succeeded + (partial if config.accept_partial_results else ())
        deadline_reached = now >= config.deadline_at

        satisfied, outcome, reason = self._decide(
            config,
            succeeded=succeeded,
            usable=usable,
            pending=pending,
            absent=absent,
            failed=failed,
            timed_out=timed_out,
            cancelled=cancelled,
            deadline_reached=deadline_reached,
        )
        missing = tuple(sorted(set(absent + pending))) if outcome != "wait" else absent
        decision_id = self._decision_id(
            config,
            state_version=state_version,
            outcome=outcome,
            included=usable if satisfied else (),
            missing=missing,
            failed=failed,
            timed_out=timed_out,
            cancelled=cancelled,
        )
        merge_refs = tuple(
            by_id[item].result_envelope_ref
            for item in usable
            if by_id[item].result_envelope_ref is not None
        )
        decision = JoinDecision(
            join_decision_id=decision_id,
            parent_run_id=config.parent_run_id,
            plan_id=config.plan_id,
            policy=config.policy,
            required_task_ids=config.required_child_run_ids,
            optional_task_ids=tuple(
                item for item in expected if item not in config.required_child_run_ids
            ),
            accepted=succeeded,
            partial=partial,
            failed=failed,
            timed_out=timed_out,
            cancelled=cancelled,
            missing=missing,
            minimum_success=config.minimum_success,
            deadline_at=config.deadline_at,
            satisfied=satisfied,
            outcome=outcome,
            reason_code=reason,
            merge_input_refs=merge_refs if satisfied else (),
            decided_at=now,
            state_version=state_version,
        )
        manifest = self._manifest(decision, by_id) if outcome == "merge" else None
        cancel = (
            tuple(item for item in pending if item not in usable)
            if config.policy == "first_success" and satisfied
            else ()
        )
        return JoinEvaluation(
            decision=decision,
            merge_manifest=manifest,
            cancel_child_run_ids=cancel,
            next_node={
                "wait": "wait",
                "merge": "merge",
                "human_review": "human_review",
                "fail": "stop",
            }[outcome],
        )

    @staticmethod
    def _decide(
        config: JoinPolicyConfig,
        *,
        succeeded: tuple[str, ...],
        usable: tuple[str, ...],
        pending: tuple[str, ...],
        absent: tuple[str, ...],
        failed: tuple[str, ...],
        timed_out: tuple[str, ...],
        cancelled: tuple[str, ...],
        deadline_reached: bool,
    ) -> tuple[bool, Literal["wait", "merge", "human_review", "fail"], str]:
        terminal_problem = set(failed + timed_out + cancelled)
        required_problem = terminal_problem & set(config.required_child_run_ids)
        required_missing = set(config.required_child_run_ids) & set(absent)
        action = config.unsatisfied_action
        if config.policy == "all_required":
            if required_problem:
                return False, action, "required_child_failed"
            if required_missing or set(config.required_child_run_ids) & set(pending):
                if deadline_reached:
                    return False, action, "required_child_missing_at_deadline"
                return False, "wait", "required_children_pending"
            if set(config.required_child_run_ids).issubset(set(succeeded)):
                return True, "merge", "all_required_succeeded"
            return False, action, "required_child_not_successful"
        if config.policy == "partial_allowed":
            minimum = config.minimum_success or 1
            if len(usable) >= minimum:
                return True, "merge", "minimum_success_reached"
            if pending and not deadline_reached:
                return False, "wait", "minimum_success_pending"
            return False, action, "minimum_success_unreachable"
        if config.policy == "first_success":
            if succeeded:
                return True, "merge", "first_success_reached"
            if pending and not deadline_reached:
                return False, "wait", "first_success_pending"
            return False, action, "no_successful_child"
        if not deadline_reached:
            return False, "wait", "deadline_not_reached"
        if usable:
            return True, "merge", "deadline_reached_with_results"
        return False, action, "deadline_reached_without_results"

    @staticmethod
    def _decision_id(config: JoinPolicyConfig, **values: object) -> str:
        digest = canonical_json_sha256(
            {"parent": config.parent_run_id, "plan": config.plan_id, "policy": config.policy, **values}
        )
        return f"join:{digest[:32]}"

    @staticmethod
    def _manifest(decision: JoinDecision, by_id: dict[str, JoinChild]) -> MergeManifest:
        included = decision.accepted + decision.partial
        return MergeManifest(
            parent_run_id=decision.parent_run_id,
            join_decision_id=decision.join_decision_id,
            inputs=tuple(
                MergeManifestItem(
                    child_run_id=child_id,
                    step_id=by_id[child_id].step_id,
                    result_envelope_ref=by_id[child_id].result_envelope_ref or "",
                    result_status="partial" if child_id in decision.partial else "succeeded",
                )
                for child_id in included
            ),
            failed=decision.failed,
            timed_out=decision.timed_out,
            cancelled=decision.cancelled,
            missing=decision.missing,
        )

    def _from_committed(
        self, decision: JoinDecision, children: tuple[JoinChild, ...]
    ) -> JoinEvaluation:
        by_id = {item.child_run_id: item for item in children}
        manifest = (
            self._manifest(decision, by_id)
            if decision.outcome == "merge"
            else None
        )
        return JoinEvaluation(
            decision=decision,
            merge_manifest=manifest,
            cancel_child_run_ids=(),
            next_node={
                "merge": "merge",
                "human_review": "human_review",
                "fail": "stop",
                "wait": "wait",
            }[decision.outcome],
        )
