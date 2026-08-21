from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pydantic import Field

from starter_agent.capabilities.models import BoundedJsonObject, Identifier, UtcDateTime, canonical_json_sha256
from starter_agent.delegation.models import BudgetLimits, ChildRun, DelegationModel, DelegationReceipt, FailureBehavior, TaskContract
from starter_agent.delegation.registry import SpecialistRegistry, SpecialistRegistryError
from starter_agent.delegation.store import OutboxMessage, RecordNotFoundError, RunEvent, SQLiteRunStore
from starter_agent.delegation.dispatcher import DEFAULT_QUEUE_HARD_CAPACITY


class CoordinatorTaskContract(DelegationModel):
    """Coordinator-owned fields; runtime identity and Registry policy are excluded."""

    goal: str = Field(min_length=1, max_length=4_000)
    inputs: BoundedJsonObject
    constraints: BoundedJsonObject = Field(default_factory=dict)
    requested_allowed_tools: tuple[Identifier, ...] = Field(default_factory=tuple, max_length=64)
    requested_deadline: UtcDateTime
    requested_budget: BudgetLimits
    failure_behavior: FailureBehavior
    idempotency_key: Identifier
    contract_version: str = Field(default="1", min_length=1, max_length=32)


class DelegationService:
    def __init__(self, *, store: SQLiteRunStore, registry: SpecialistRegistry, now: Callable[[], datetime] | None = None, queue_hard_capacity: int = DEFAULT_QUEUE_HARD_CAPACITY) -> None:
        self.store = store
        self.registry = registry
        self._now = now or (lambda: datetime.now(UTC))
        self.queue_hard_capacity = queue_hard_capacity

    def delegate_task(self, *, parent_run_id: str, specialist_id: str, task_contract: CoordinatorTaskContract) -> DelegationReceipt:
        now = self._now()
        coordinator_payload = task_contract.model_dump(mode="json")
        inputs = coordinator_payload["inputs"]
        constraints = coordinator_payload["constraints"]
        parent = self.store.get_parent(parent_run_id)
        if parent is None:
            raise RecordNotFoundError(f"Parent run not found: {parent_run_id}")
        specialist, registry_snapshot = self.registry.resolve_with_snapshot(
            specialist_id, inputs=inputs, requested_budget=task_contract.requested_budget
        )
        if not set(task_contract.requested_allowed_tools).issubset(specialist.allowed_tools):
            raise SpecialistRegistryError("specialist_tool_scope_exceeded", "requested tools exceed specialist permissions")
        snapshot_id = registry_snapshot.snapshot_hash
        seed = canonical_json_sha256({"parent_run_id": parent_run_id, "specialist_id": specialist_id, "idempotency_key": task_contract.idempotency_key})
        task_id = f"task:{seed[:32]}"
        child_run_id = f"child-run:{seed[32:]}"
        deadline = min(task_contract.requested_deadline, parent.deadline_at, now + timedelta(milliseconds=specialist.default_deadline_ms))
        contract = TaskContract(
            task_id=task_id, parent_run_id=parent_run_id, specialist_id=specialist_id,
            goal=task_contract.goal, inputs=inputs, constraints=constraints,
            requested_allowed_tools=task_contract.requested_allowed_tools, requested_deadline=deadline,
            requested_budget=task_contract.requested_budget, failure_behavior=task_contract.failure_behavior,
            idempotency_key=task_contract.idempotency_key, contract_version=task_contract.contract_version,
        )
        run = ChildRun(id=child_run_id, child_task_id=task_id, parent_run_id=parent_run_id, attempt=1, status="queued", phase="queued", deadline_at=deadline, created_at=now, updated_at=now)
        event = RunEvent(
            id=f"event:{seed}", parent_run_id=parent_run_id, child_run_id=child_run_id,
            event_type="child.delegated", status="queued", occurred_at=now,
            payload={"task_id": task_id, "specialist_id": specialist_id, "specialist_version": specialist.version, "specialist_snapshot_id": snapshot_id, "contract_hash": contract.canonical_hash},
        )
        outbox = OutboxMessage(
            id=f"outbox:{seed}", topic="delegation.child.queued", aggregate_id=child_run_id,
            idempotency_key=f"child-queued:{seed}",
            payload={"parent_run_id": parent_run_id, "task_id": task_id, "child_run_id": child_run_id, "specialist_snapshot_id": snapshot_id},
            created_at=now,
        )
        created = self.store.create_child_task_and_run(
            contract=contract, child_run=run, specialist_snapshot_id=snapshot_id,
            output_schema_version=specialist.schema_version, expected_parent_version=parent.version,
            created_at=now, creation_event=event, outbox_message=outbox,
            queue_hard_capacity=self.queue_hard_capacity,
        )
        return DelegationReceipt(
            receipt_id=f"receipt:{seed}", parent_run_id=parent_run_id, task_id=created.task.id,
            child_run_id=created.run.id, specialist_id=created.task.specialist_id,
            specialist_snapshot_id=created.task.specialist_snapshot_id, created_at=created.task.created_at,
        )


class DelegationResumeService:
    """Authority-bound production entry for resuming a suspended Child."""

    def __init__(self, *, store: SQLiteRunStore, now: Callable[[], datetime] | None = None, timeout_seconds: int = 900) -> None:
        self.store = store
        self._now = now or (lambda: datetime.now(UTC))
        self.timeout_seconds = timeout_seconds

    def resume(self, *, parent_run_id: str, child_task_id: str, child_run_id: str, principal: str, checkpoint_ref: str) -> ChildRun:
        return self.store.resume_child_from_checkpoint(
            checkpoint_ref, parent_run_id=parent_run_id, child_task_id=child_task_id,
            child_run_id=child_run_id, principal=principal, now=self._now(),
            timeout_seconds=self.timeout_seconds,
        )
