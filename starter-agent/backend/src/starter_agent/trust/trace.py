from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from starter_agent.capabilities.models import AuditEvent, canonical_json_sha256
from starter_agent.trust.models import TrustTraceEvent
from starter_agent.trust.redaction import redact_trust_payload
from starter_agent.trust.store import TrustStore


@dataclass(frozen=True, slots=True)
class TraceContext:
    eval_run_id: str | None = None
    case_id: str | None = None
    session_id: str | None = None
    turn_id: str | None = None
    model_request_id: str | None = None
    tool_call_id: str | None = None
    policy_decision_id: str | None = None
    approval_id: str | None = None
    child_run_id: str | None = None
    parent_run_id: str | None = None
    child_task_id: str | None = None
    principal: str | None = None
    access_level: str | None = None

    def merge(self, **changes: str | None) -> "TraceContext":
        values = {
            "eval_run_id": self.eval_run_id,
            "case_id": self.case_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "model_request_id": self.model_request_id,
            "tool_call_id": self.tool_call_id,
            "policy_decision_id": self.policy_decision_id,
            "approval_id": self.approval_id,
            "child_run_id": self.child_run_id,
            "parent_run_id": self.parent_run_id,
            "child_task_id": self.child_task_id,
            "principal": self.principal,
            "access_level": self.access_level,
        }
        values.update({key: value for key, value in changes.items() if value is not None})
        return TraceContext(**values)


class TrustTraceRecorder:
    def __init__(self, store: TrustStore) -> None:
        self.store = store

    def record(
        self,
        *,
        id: str,
        context: TraceContext,
        event_type: str,
        status: str,
        summary: dict[str, Any],
        payload: dict[str, Any] | None = None,
        parent_event_id: str | None = None,
        occurred_at: datetime | None = None,
        source_ref: str | None = None,
    ) -> TrustTraceEvent:
        safe_summary = redact_trust_payload(summary)
        payload_value = redact_trust_payload(payload if payload is not None else summary)
        event = TrustTraceEvent(
            id=id,
            eval_run_id=context.eval_run_id,
            case_id=context.case_id,
            session_id=context.session_id,
            turn_id=context.turn_id,
            model_request_id=context.model_request_id,
            tool_call_id=context.tool_call_id,
            policy_decision_id=context.policy_decision_id,
            approval_id=context.approval_id,
            child_run_id=context.child_run_id,
            parent_run_id=context.parent_run_id,
            child_task_id=context.child_task_id,
            principal=context.principal,
            access_level=context.access_level,
            parent_event_id=parent_event_id,
            event_type=event_type,
            status=status,
            occurred_at=occurred_at or datetime.now(UTC),
            summary=safe_summary,
            payload_hash=canonical_json_sha256(payload_value),
            source_ref=source_ref,
        )
        return self.store.append_trace_event(event)

    def from_audit_event(
        self,
        audit_event: AuditEvent,
        *,
        context: TraceContext,
    ) -> TrustTraceEvent:
        payload = dict(audit_event.payload)
        active = self._active_run_context()
        if active is not None and active.child_task_id is not None:
            payload = {
                **payload,
                "parent_run_id": active.parent_run_id,
                "child_task_id": active.child_task_id,
                "child_run_id": active.trace_context.child_run_id,
                "eval_run_id": active.trace_context.eval_run_id,
                "case_id": active.trace_context.case_id,
                "model_request_id": active.trace_context.model_request_id,
                "principal": active.principal,
                "access_level": "child_restricted",
                "policy_decision_id": (
                    payload.get("policy_decision_id")
                    or active.trace_context.policy_decision_id
                ),
                "approval_id": (
                    payload.get("approval_id")
                    or active.trace_context.approval_id
                ),
            }
        merged = context.merge(
            eval_run_id=self._string(payload.get("eval_run_id")),
            case_id=self._string(payload.get("case_id")),
            # AuditEvent identifiers are trusted envelope fields.  A tool's
            # payload is untrusted observation data and can only fill a
            # missing envelope field, never replace it.
            session_id=(
                self._string(audit_event.session_id)
                or self._string(payload.get("session_id"))
            ),
            turn_id=(
                self._string(audit_event.turn_id)
                or self._string(payload.get("turn_id"))
            ),
            tool_call_id=(
                self._string(audit_event.call_id)
                or self._string(payload.get("call_id") or payload.get("tool_call_id"))
            ),
            model_request_id=self._string(payload.get("model_request_id")),
            policy_decision_id=self._string(payload.get("policy_decision_id")),
            approval_id=self._string(payload.get("approval_id") or payload.get("confirmation_id")),
            child_run_id=self._string(payload.get("child_run_id")),
            parent_run_id=self._string(payload.get("parent_run_id")),
            child_task_id=self._string(payload.get("child_task_id")),
            principal=self._string(payload.get("principal")),
            access_level=self._string(payload.get("access_level")),
        )
        summary = {
            "source_audit_action": audit_event.action,
            "target": audit_event.target,
            "decision": audit_event.decision,
            "reason_code": audit_event.reason_code,
            **self._safe_payload_summary(payload),
            "missing_nodes": self._missing_nodes(merged),
        }
        return self.record(
            id=audit_event.event_id,
            context=merged,
            event_type=self._event_type(audit_event.action),
            status=self._status(audit_event),
            summary=summary,
            payload=payload,
            occurred_at=audit_event.created_at,
            source_ref=f"capability_audit:{audit_event.event_id}",
        )

    def _safe_payload_summary(self, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = (
            "tool_name",
            "server_id",
            "schema_hash",
            "snapshot_id",
            "source_url",
            "final_url",
            "error_code",
        )
        return {key: payload[key] for key in allowed if key in payload}

    def _missing_nodes(self, context: TraceContext) -> dict[str, str]:
        required = (
            "model_request_id",
            "tool_call_id",
            "policy_decision_id",
            "approval_id",
        )
        return {
            field: "not present in source audit event"
            for field in required
            if getattr(context, field) is None
        }

    def _event_type(self, action: str) -> str:
        if action.startswith("model."):
            return "Model"
        if action.startswith("tool.") or action.startswith("permit."):
            return "Tool"
        if action.startswith("gate.") or action.startswith("policy."):
            return "Policy"
        if "confirmation" in action or action.startswith("approval."):
            return "Approval"
        if action.startswith("memory.") or action.startswith("context."):
            return "Memory / Context"
        if action.endswith(".error"):
            return "Error"
        return "Run"

    def _status(self, audit_event: AuditEvent) -> str:
        if audit_event.decision in {"deny", "blocked"}:
            return "blocked"
        if audit_event.action.endswith(".error"):
            return "error"
        return "completed"

    def _string(self, value: Any) -> str | None:
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _active_run_context():
        # Avoid a module import cycle: runtime imports delegation and tracing is
        # intentionally optional observability infrastructure.
        try:
            from starter_agent.agent.runtime import _ACTIVE_RUN_CONTEXT
            return _ACTIVE_RUN_CONTEXT.get()
        except ImportError:
            return None


class CapabilityAuditTrustBridge:
    """Records existing capability audit events in the Trust trace store."""

    def __init__(self, store: TrustStore) -> None:
        self.recorder = TrustTraceRecorder(store)

    def record(self, audit_event: AuditEvent) -> TrustTraceEvent:
        return self.recorder.from_audit_event(audit_event, context=TraceContext())


@dataclass(frozen=True, slots=True)
class DelegationTraceSync:
    """Result of incrementally reflecting existing RunStore evidence."""

    events: tuple[TrustTraceEvent, ...]
    next_cursor: str | None


class DelegationEventTrustBridge:
    """Project the durable delegation event log into the existing Trust store.

    This is a read-only adapter over ``SQLiteRunStore.list_events``.  The
    RunStore remains the source of state, budget, lease, merge and route
    evidence; Trust receives only a redacted, idempotent correlation record.
    """

    _CATEGORY = {
        "orchestration": "Orchestration",
        "budget": "Budget",
        "lease": "Lease",
        "validator": "Validator",
        "result": "Result",
        "merge": "Merger",
        "backfill": "Backfill",
        "route": "Route",
        "legacy": "Route",
        "subagent": "Delegation",
        "child": "Delegation",
        "parent": "Delegation",
        "run": "Run",
    }
    _SUMMARY_FIELDS = (
        "task_id", "registry_hash", "registry_snapshot_hash", "specialist_snapshot_id",
        "specialist_version", "contract_hash", "tool_view_hash", "effective_tool_view_hash",
        "route", "legacy_path_used", "subagent_call_id", "model_request_id",
        "policy_decision_id", "approval_id", "error_code", "reason", "reason_code",
        "from", "to", "version", "merge_report_id", "result_version", "result_hash",
        "envelope_hash", "envelope_ref", "checkpoint_ref", "cancellation_version",
    )
    _ORCHESTRATION_SUMMARY_FIELDS = _SUMMARY_FIELDS + (
        "route_decision_id", "plan_id", "step_id", "parent_run_id",
        "child_run_id", "task_event_id", "join_decision_id", "verify_id",
        "recovery_id", "budget_snapshot_id", "model_decision_id",
        "pending_action_id", "attempt", "duration_ms", "decision",
        "fallback", "stop_reason_code",
    )

    def __init__(self, run_store, trust_store: TrustStore) -> None:
        self.run_store = run_store
        self.recorder = TrustTraceRecorder(trust_store)

    def sync_parent(
        self, parent_run_id: str, *, after_seq: int = 0, limit: int = 500
    ) -> DelegationTraceSync:
        page = self.run_store.list_events(parent_run_id, after_seq=after_seq, limit=limit)
        tree = self.run_store.get_run_tree(parent_run_id)
        task_by_run = {run.id: run.child_task_id for run in tree.child_runs}
        events = tuple(
            self._record(run_event, parent=tree.parent, child_task_id=task_by_run.get(run_event.child_run_id))
            for run_event in page.items
        )
        return DelegationTraceSync(events=events, next_cursor=page.next_cursor)

    def record(self, run_event) -> TrustTraceEvent:
        """Project a just-committed event; failures are handled by RunStore."""
        tree = self.run_store.get_run_tree(run_event.parent_run_id)
        task_by_run = {run.id: run.child_task_id for run in tree.child_runs}
        return self._record(
            run_event, parent=tree.parent,
            child_task_id=task_by_run.get(run_event.child_run_id),
        )

    def backfill_parent(
        self, parent_run_id: str, *, after_seq: int = 0, page_size: int = 500
    ) -> DelegationTraceSync:
        """Idempotently replay durable evidence; it never invokes a Worker."""
        projected: list[TrustTraceEvent] = []
        cursor = after_seq
        while True:
            page = self.sync_parent(parent_run_id, after_seq=cursor, limit=page_size)
            projected.extend(page.events)
            if page.next_cursor is None:
                break
            cursor = int(page.next_cursor)
        from datetime import UTC, datetime
        from starter_agent.delegation.store import RunEvent
        backfill_id = f"trust-backfill:{parent_run_id}:{after_seq}"
        if all(event.id != backfill_id for event in self.run_store.get_run_tree(parent_run_id).events):
            self.run_store.append_event(RunEvent(
                id=backfill_id, parent_run_id=parent_run_id,
                event_type="trust.backfill", status="completed", occurred_at=datetime.now(UTC),
                payload={
                    "after_seq": after_seq,
                    "projected_count": len(projected),
                },
            ))
        return DelegationTraceSync(events=tuple(projected), next_cursor=None)

    def backfill_recent(
        self, *, parent_page_size: int = 500, event_page_size: int = 500
    ) -> tuple[DelegationTraceSync, ...]:
        projected: list[DelegationTraceSync] = []
        parent_cursor: str | None = None
        while True:
            parent_page = self.run_store.list_parent_run_ids_page(
                after_id=parent_cursor, limit=parent_page_size
            )
            projected.extend(
                self.backfill_parent(parent_id, page_size=event_page_size)
                for parent_id in parent_page.items
            )
            if parent_page.next_cursor is None:
                break
            parent_cursor = parent_page.next_cursor
        return tuple(projected)

    def _record(self, run_event, *, parent, child_task_id: str | None) -> TrustTraceEvent:
        payload = dict(run_event.payload)
        task_id = self._string(payload.get("task_id")) or child_task_id
        child_run_id = run_event.child_run_id
        context = TraceContext(
            session_id=parent.session_id,
            turn_id=parent.origin_turn_id,
            model_request_id=self._string(payload.get("model_request_id")),
            policy_decision_id=self._string(payload.get("policy_decision_id")),
            approval_id=self._string(payload.get("approval_id")),
            parent_run_id=parent.id,
            child_task_id=task_id,
            child_run_id=child_run_id,
            principal=parent.principal,
            access_level="delegation_restricted",
        )
        summary = {"delegation_event_type": run_event.event_type, "event_seq": run_event.event_seq}
        summary_fields = (
            self._ORCHESTRATION_SUMMARY_FIELDS
            if run_event.event_type.startswith("orchestration.")
            else self._SUMMARY_FIELDS
        )
        summary.update({key: payload[key] for key in summary_fields if key in payload})
        return self.recorder.record(
            id=f"delegation:{run_event.id}",
            context=context,
            event_type=self._event_type(run_event.event_type),
            status=run_event.status,
            summary=summary,
            payload=payload,
            occurred_at=run_event.occurred_at,
            source_ref=f"delegation_run_event:{run_event.id}",
        )

    def _event_type(self, event_type: str) -> str:
        prefix = event_type.split(".", 1)[0].casefold()
        return self._CATEGORY.get(prefix, "Delegation")

    @staticmethod
    def _string(value: Any) -> str | None:
        return value if isinstance(value, str) and value else None
