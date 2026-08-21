from __future__ import annotations

import hashlib
from datetime import datetime

from pydantic import Field

from starter_agent.capabilities.models import canonical_json_sha256
from starter_agent.delegation.models import BudgetLimits, ParentRun
from starter_agent.delegation.store import (
    RecordAlreadyExistsError,
    RecordNotFoundError,
    RunEvent,
    SQLiteRunStore,
)
from starter_agent.orchestration.budget import to_delegation_budget
from starter_agent.orchestration.models import (
    BackgroundTask,
    BudgetAmounts,
    ExecutionState,
    OrchestrationModel,
)


class BackgroundTaskConflict(ValueError):
    pass


class BackgroundTaskSpec(OrchestrationModel):
    state: ExecutionState
    principal: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=160)
    runtime_revision: str = Field(min_length=1, max_length=200)
    coordinator_spec_version: str = Field(min_length=1, max_length=200)
    deadline_at: datetime
    priority: int = Field(default=100, ge=0, le=1_000)


class BackgroundTaskService:
    """Task-level durable facade over the existing SQLiteRunStore."""

    def __init__(self, store: SQLiteRunStore) -> None:
        self.store = store

    def create(
        self,
        spec: BackgroundTaskSpec,
        *,
        created_at: datetime,
    ) -> BackgroundTask:
        suffix = hashlib.sha256(spec.idempotency_key.encode("utf-8")).hexdigest()[:24]
        task_id = f"task:orch:{suffix}"
        parent_run_id = f"parent:orch:{suffix}"
        request_hash = canonical_json_sha256(spec.model_dump(mode="json"))
        existing = self.store.get_parent(parent_run_id)
        if existing is not None:
            if existing.request_hash != request_hash:
                raise BackgroundTaskConflict("idempotency_payload_conflict")
            return self._task(existing)

        state = self._bind_state(
            spec.state,
            task_id=task_id,
            parent_run_id=parent_run_id,
            created_at=created_at,
        )
        limit = (
            BudgetAmounts()
            if state.budget is None
            else state.budget.limit
        )
        zero = BudgetLimits(
            steps=0,
            tokens=0,
            cost_microunits=0,
            wall_clock_ms=0,
            model_calls=0,
            tool_calls=0,
        )
        parent = ParentRun(
            id=parent_run_id,
            run_type="job_application_orchestration",
            session_id=state.session_id,
            origin_turn_id=state.turn_id,
            principal=spec.principal,
            coordinator_spec_version=spec.coordinator_spec_version,
            runtime_revision=spec.runtime_revision,
            status="queued",
            phase="queued",
            priority=spec.priority,
            available_at=created_at,
            deadline_at=spec.deadline_at,
            budget_total=to_delegation_budget(limit),
            budget_reserved=zero,
            budget_consumed=zero,
            route=state.route.route if state.route is not None else "unrouted",
            task_id=task_id,
            request_hash=request_hash,
            route_decision_id=(
                None if state.route is None else state.route.route_decision_id
            ),
            orchestration_state_version=state.state_version,
            orchestration_state=state.model_dump(mode="json"),
            plan_id=None if state.plan is None else state.plan.plan_id,
            current_step_id=state.current_step,
            latest_budget_snapshot_id=(
                None if state.budget is None else state.budget.budget_snapshot_id
            ),
            created_at=created_at,
            updated_at=created_at,
        )
        event = RunEvent(
            id=f"background-created:{task_id}",
            parent_run_id=parent_run_id,
            event_type="background_task.created",
            status="completed",
            occurred_at=created_at,
            payload={
                "task_id": task_id,
                "route": parent.route,
                "version": parent.version,
            },
        )
        try:
            self.store.create_parent_with_event(parent, event)
        except RecordAlreadyExistsError:
            concurrent = self.store.get_parent(parent_run_id)
            if concurrent is None or concurrent.request_hash != request_hash:
                raise BackgroundTaskConflict("idempotency_payload_conflict")
            parent = concurrent
        return self._task(parent)

    def get(self, task_id: str) -> BackgroundTask:
        parent = self.store.get_parent(_parent_id(task_id))
        if parent is None or parent.task_id != task_id:
            raise RecordNotFoundError(f"Background task not found: {task_id}")
        return self._task(parent)

    def transition(
        self,
        task_id: str,
        public_status: str,
        *,
        expected_version: int,
        occurred_at: datetime,
        reason_code: str | None = None,
        phase: str | None = None,
    ) -> BackgroundTask:
        parent = self.store.transition_orchestration_parent(
            _parent_id(task_id),
            public_status=public_status,
            phase=phase or public_status,
            reason_code=reason_code,
            expected_version=expected_version,
            occurred_at=occurred_at,
        )
        return self._task(parent)

    def mark_interrupted(
        self,
        task_id: str,
        *,
        expected_version: int,
        occurred_at: datetime,
        reason_code: str = "process_interrupted",
    ) -> BackgroundTask:
        return self.transition(
            task_id,
            "interrupted",
            expected_version=expected_version,
            occurred_at=occurred_at,
            reason_code=reason_code,
            phase="interrupted",
        )

    @staticmethod
    def _bind_state(
        state: ExecutionState,
        *,
        task_id: str,
        parent_run_id: str,
        created_at: datetime,
    ) -> ExecutionState:
        route = (
            None
            if state.route is None
            else state.route.model_copy(update={"run_id": parent_run_id})
        )
        budget = (
            None
            if state.budget is None
            else state.budget.model_copy(update={"parent_run_id": parent_run_id})
        )
        task = BackgroundTask(
            task_id=task_id,
            parent_run_id=parent_run_id,
            session_id=state.session_id,
            origin_turn_id=state.turn_id,
            status="queued",
            internal_status="queued",
            phase="queued",
            budget_snapshot_id=(
                "budget:unallocated"
                if budget is None
                else budget.budget_snapshot_id
            ),
            created_at=created_at,
            updated_at=created_at,
        )
        return ExecutionState.model_validate(
            {
                **state.model_dump(mode="python"),
                "run_id": parent_run_id,
                "parent_run_id": parent_run_id,
                "route": route,
                "budget": budget,
                "background_task": task,
            }
        )

    @staticmethod
    def _task(parent: ParentRun) -> BackgroundTask:
        state = ExecutionState.model_validate(parent.orchestration_state or {})
        snapshot = state.background_task
        if snapshot is None or parent.task_id is None:
            raise BackgroundTaskConflict("background_task_snapshot_missing")
        public_status = {
            "created": "queued",
            "queued": "queued",
            "running": "running",
            "waiting_children": "waiting",
            "waiting_for_user": "waiting",
            "cancelling": "waiting",
            "succeeded": "completed",
            "partial": "partial",
            "failed": (
                "interrupted"
                if parent.stop_reason_code == "process_interrupted"
                else "failed"
            ),
            "timed_out": "failed",
            "budget_exhausted": "failed",
            "cancelled": "cancelled",
        }[parent.status]
        return snapshot.model_copy(
            update={
                "status": public_status,
                "internal_status": parent.status,
                "reason_code": parent.stop_reason_code,
                "phase": parent.phase,
                "plan_id": parent.plan_id,
                "current_step_id": parent.current_step_id,
                "budget_snapshot_id": (
                    parent.latest_budget_snapshot_id
                    or snapshot.budget_snapshot_id
                ),
                "created_at": parent.created_at,
                "started_at": parent.started_at,
                "updated_at": parent.updated_at,
                "completed_at": parent.completed_at,
                "version": parent.version,
            }
        )


def _parent_id(task_id: str) -> str:
    prefix = "task:orch:"
    if not task_id.startswith(prefix):
        raise RecordNotFoundError(f"Background task not found: {task_id}")
    return "parent:orch:" + task_id.removeprefix(prefix)

