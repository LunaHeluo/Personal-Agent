from __future__ import annotations

from typing import Literal, Mapping

from pydantic import Field

from starter_agent.capabilities.models import canonical_json_sha256
from starter_agent.delegation.dispatcher import Dispatcher
from starter_agent.delegation.store import RunEvent, SQLiteRunStore
from starter_agent.orchestration.models import (
    ChildRunSnapshot,
    OrchestrationModel,
    TaskEvent,
)


_TERMINAL_EVENTS = {
    "child_completed": "succeeded",
    "child_failed": "failed",
    "child_cancelled": "cancelled",
    "child_timed_out": "timed_out",
}
_TERMINAL_STATUSES = frozenset(_TERMINAL_EVENTS.values())


class TaskEventConflict(ValueError):
    pass


class TaskManagerState(OrchestrationModel):
    parent_run_id: str = Field(min_length=1, max_length=160)
    last_event_seq: int = Field(default=0, ge=0)
    child_runs: tuple[ChildRunSnapshot, ...] = Field(default_factory=tuple, max_length=10_000)
    buffered_events: tuple[TaskEvent, ...] = Field(default_factory=tuple, max_length=10_000)
    event_hashes: Mapping[str, str] = Field(default_factory=dict)


class TaskEventApplication(OrchestrationModel):
    disposition: Literal["applied", "duplicate", "buffered", "late_ignored"]
    state: TaskManagerState
    applied_event_ids: tuple[str, ...] = ()


class ConcurrencyLimits(OrchestrationModel):
    global_limit: int = Field(ge=1, le=10_000)
    parent_limit: int = Field(ge=1, le=1_000)
    specialist_limits: Mapping[str, int] = Field(default_factory=dict)
    provider_limits: Mapping[str, int] = Field(default_factory=dict)
    tool_limits: Mapping[str, int] = Field(default_factory=dict)


class CapacitySnapshot(OrchestrationModel):
    global_active: int = Field(default=0, ge=0)
    parent_active: Mapping[str, int] = Field(default_factory=dict)
    specialist_active: Mapping[str, int] = Field(default_factory=dict)
    provider_active: Mapping[str, int] = Field(default_factory=dict)
    tool_active: Mapping[str, int] = Field(default_factory=dict)


class AdmissionDecision(OrchestrationModel):
    admitted: bool
    reason_code: str


class CapacityGovernor:
    """Pure admission edge fed by durable/worker capacity snapshots."""

    def __init__(self, limits: ConcurrencyLimits) -> None:
        self._limits = limits

    def admit(
        self,
        snapshot: CapacitySnapshot,
        *,
        parent_run_id: str,
        specialist_id: str,
        provider: str,
        tools: tuple[str, ...],
    ) -> AdmissionDecision:
        checks = (
            (snapshot.global_active, self._limits.global_limit, "global_backpressure"),
            (
                snapshot.parent_active.get(parent_run_id, 0),
                self._limits.parent_limit,
                "parent_backpressure",
            ),
            (
                snapshot.specialist_active.get(specialist_id, 0),
                self._limits.specialist_limits.get(specialist_id),
                "specialist_backpressure",
            ),
            (
                snapshot.provider_active.get(provider, 0),
                self._limits.provider_limits.get(provider),
                "provider_backpressure",
            ),
        )
        for active, limit, reason in checks:
            if limit is not None and active >= limit:
                return AdmissionDecision(admitted=False, reason_code=reason)
        for tool in tools:
            limit = self._limits.tool_limits.get(tool)
            if limit is not None and snapshot.tool_active.get(tool, 0) >= limit:
                return AdmissionDecision(admitted=False, reason_code="tool_backpressure")
        return AdmissionDecision(admitted=True, reason_code="capacity_available")


class TaskEventReducer:
    """Deterministic event reducer; it never invokes a model or polls a Child."""

    def apply(self, state: TaskManagerState, event: TaskEvent) -> TaskEventApplication:
        if event.parent_run_id != state.parent_run_id:
            raise TaskEventConflict("task_event_parent_mismatch")
        known_hash = state.event_hashes.get(event.task_event_id)
        if known_hash is not None:
            if known_hash != event.payload_hash:
                raise TaskEventConflict("task_event_idempotency_conflict")
            return TaskEventApplication(disposition="duplicate", state=state)
        event_hashes = {**state.event_hashes, event.task_event_id: event.payload_hash}
        if event.event_seq <= state.last_event_seq:
            return TaskEventApplication(
                disposition="late_ignored",
                state=state.model_copy(update={"event_hashes": event_hashes}),
            )
        if event.event_seq > state.last_event_seq + 1:
            buffered = tuple(
                sorted((*state.buffered_events, event), key=lambda item: item.event_seq)
            )
            return TaskEventApplication(
                disposition="buffered",
                state=state.model_copy(
                    update={"buffered_events": buffered, "event_hashes": event_hashes}
                ),
            )
        next_state = state.model_copy(update={"event_hashes": event_hashes})
        next_state = self._apply_contiguous(next_state, event)
        applied = [event.task_event_id]
        while next_state.buffered_events and next_state.buffered_events[0].event_seq == next_state.last_event_seq + 1:
            pending = next_state.buffered_events[0]
            next_state = next_state.model_copy(
                update={"buffered_events": next_state.buffered_events[1:]}
            )
            next_state = self._apply_contiguous(next_state, pending)
            applied.append(pending.task_event_id)
        return TaskEventApplication(
            disposition="applied",
            state=next_state,
            applied_event_ids=tuple(applied),
        )

    @staticmethod
    def _apply_contiguous(state: TaskManagerState, event: TaskEvent) -> TaskManagerState:
        children = {item.child_run_id: item for item in state.child_runs}
        if event.child_run_id is not None:
            current = children.get(event.child_run_id)
            if current is None:
                current = ChildRunSnapshot(
                    child_run_id=event.child_run_id,
                    child_task_id=event.task_id,
                    parent_run_id=event.parent_run_id,
                    plan_step_id=event.step_id,
                    attempt=event.attempt,
                    status="queued",
                    phase="queued",
                )
            if current.status not in _TERMINAL_STATUSES:
                status = {
                    "child_started": "running",
                    "child_progress": event.status,
                    **_TERMINAL_EVENTS,
                }.get(event.event_type, current.status)
                if event.event_type == "child_completed" and event.status in {
                    "succeeded",
                    "partial",
                }:
                    status = event.status
                result_ref = (
                    event.artifact_refs[0]
                    if event.event_type == "child_completed" and event.artifact_refs
                    else current.result_envelope_ref
                )
                current = current.model_copy(
                    update={
                        "status": status,
                        "phase": event.event_type,
                        "attempt": event.attempt,
                        "result_envelope_ref": result_ref,
                        "stop_reason_code": (
                            str(event.payload_summary.get("reason_code"))
                            if event.event_type in _TERMINAL_EVENTS
                            and event.payload_summary.get("reason_code")
                            else current.stop_reason_code
                        ),
                        "last_task_event_seq": event.event_seq,
                    }
                )
                children[event.child_run_id] = current
        return state.model_copy(
            update={
                "last_event_seq": event.event_seq,
                "child_runs": tuple(sorted(children.values(), key=lambda item: item.child_run_id)),
            }
        )


class OrchestrationTaskManager:
    """Persist events in RunStore and delegate leases/retries/cancel to Dispatcher."""

    def __init__(self, *, store: SQLiteRunStore, dispatcher: Dispatcher) -> None:
        self._store = store
        self._dispatcher = dispatcher
        self._reducer = TaskEventReducer()

    def accept_event(
        self, state: TaskManagerState, event: TaskEvent
    ) -> TaskEventApplication:
        self._store.append_event(
            RunEvent(
                id=f"task-event:{event.task_event_id}",
                parent_run_id=event.parent_run_id,
                child_run_id=event.child_run_id,
                event_type=f"orchestration.{event.event_type}",
                status=event.status,
                occurred_at=event.occurred_at,
                payload={
                    "task_event_id": event.task_event_id,
                    "source_event_seq": event.event_seq,
                    "step_id": event.step_id,
                    "attempt": event.attempt,
                    "payload_hash": event.payload_hash,
                    "late_ignored": event.late_ignored,
                },
            )
        )
        return self._reducer.apply(state, event)

    def cancel_parent(self, parent_run_id: str, *, reason: str):
        return self._dispatcher.cancel_parent(parent_run_id, reason=reason)

    def retry(self, claim, *, error_code: str, now=None):
        return self._dispatcher.retry(claim, error_code=error_code, now=now)


def make_task_event(*, payload_summary: Mapping[str, object], **values: object) -> TaskEvent:
    payload_hash = canonical_json_sha256(
        {
            "payload_summary": payload_summary,
            "artifact_refs": values.get("artifact_refs", ()),
            "status": values.get("status"),
        }
    )
    return TaskEvent(
        **values,
        payload_summary=dict(payload_summary),
        payload_hash=payload_hash,
    )
