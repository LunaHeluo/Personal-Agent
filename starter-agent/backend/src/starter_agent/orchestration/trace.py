from __future__ import annotations

from datetime import datetime
from typing import Literal, Mapping

from pydantic import Field

from starter_agent.capabilities.models import canonical_json_sha256
from starter_agent.delegation.store import RunEvent, SQLiteRunStore
from starter_agent.orchestration.models import OrchestrationModel


class OrchestrationTraceCorrelation(OrchestrationModel):
    parent_run_id: str = Field(min_length=1, max_length=160)
    route_decision_id: str | None = Field(default=None, max_length=160)
    plan_id: str | None = Field(default=None, max_length=160)
    step_id: str | None = Field(default=None, max_length=160)
    child_run_id: str | None = Field(default=None, max_length=160)
    task_event_id: str | None = Field(default=None, max_length=160)
    join_decision_id: str | None = Field(default=None, max_length=160)
    verify_id: str | None = Field(default=None, max_length=160)
    recovery_id: str | None = Field(default=None, max_length=160)
    budget_snapshot_id: str | None = Field(default=None, max_length=160)
    model_decision_id: str | None = Field(default=None, max_length=160)
    pending_action_id: str | None = Field(default=None, max_length=160)


class OrchestrationTraceRecord(OrchestrationModel):
    event_type: Literal[
        "route",
        "plan",
        "validation",
        "parent_run",
        "child_run",
        "task_event",
        "join",
        "verify",
        "recovery",
        "budget",
        "model_decision",
        "approval",
        "stop",
    ]
    status: Literal["started", "completed", "failed", "blocked", "waiting"]
    correlation: OrchestrationTraceCorrelation
    occurred_at: datetime
    duration_ms: int | None = Field(default=None, ge=0)
    attempt: int = Field(default=1, ge=1, le=100)
    reason_code: str | None = Field(default=None, max_length=200)
    decision: str | None = Field(default=None, max_length=200)
    fallback: str | None = Field(default=None, max_length=500)
    stop_reason_code: str | None = Field(default=None, max_length=200)


class OrchestrationTraceProjector:
    """Append redaction-safe correlation evidence to the existing RunStore."""

    def __init__(self, store: SQLiteRunStore) -> None:
        self._store = store

    def record(self, record: OrchestrationTraceRecord) -> RunEvent:
        correlation = record.correlation.model_dump(mode="json", exclude_none=True)
        payload = {
            **correlation,
            "attempt": record.attempt,
            **({"duration_ms": record.duration_ms} if record.duration_ms is not None else {}),
            **({"reason_code": record.reason_code} if record.reason_code else {}),
            **({"decision": record.decision} if record.decision else {}),
            **({"fallback": record.fallback} if record.fallback else {}),
            **(
                {"stop_reason_code": record.stop_reason_code}
                if record.stop_reason_code
                else {}
            ),
        }
        identity = next(
            (
                value
                for key, value in reversed(tuple(correlation.items()))
                if key != "parent_run_id"
            ),
            record.correlation.parent_run_id,
        )
        digest = canonical_json_sha256(
            {
                "type": record.event_type,
                "status": record.status,
                "identity": identity,
                "attempt": record.attempt,
            }
        )
        return self._store.append_event(
            RunEvent(
                id=f"orchestration:{record.event_type}:{digest[:32]}",
                parent_run_id=record.correlation.parent_run_id,
                child_run_id=record.correlation.child_run_id,
                event_type=f"orchestration.{record.event_type}",
                status=record.status,
                occurred_at=record.occurred_at,
                payload=payload,
            )
        )


class TraceChainAudit(OrchestrationModel):
    complete: bool
    missing_event_types: tuple[str, ...] = ()
    orphan_correlation_ids: tuple[str, ...] = ()


class TraceChainAuditor:
    """Read-only audit of a projected orchestration trace chain."""

    def audit(
        self,
        events: tuple[RunEvent, ...],
        *,
        required_event_types: tuple[str, ...],
    ) -> TraceChainAudit:
        orchestration = tuple(
            item for item in events if item.event_type.startswith("orchestration.")
        )
        present = {item.event_type.removeprefix("orchestration.") for item in orchestration}
        missing = tuple(item for item in required_event_types if item not in present)
        parent_ids = {item.parent_run_id for item in orchestration}
        orphans: list[str] = []
        if len(parent_ids) > 1:
            orphans.extend(sorted(parent_ids))
        known_plans = {
            str(item.payload["plan_id"])
            for item in orchestration
            if item.event_type == "orchestration.plan" and item.payload.get("plan_id")
        }
        for item in orchestration:
            plan_id = item.payload.get("plan_id")
            if plan_id and item.event_type != "orchestration.plan" and str(plan_id) not in known_plans:
                orphans.append(str(plan_id))
        return TraceChainAudit(
            complete=not missing and not orphans,
            missing_event_types=missing,
            orphan_correlation_ids=tuple(sorted(set(orphans))),
        )
