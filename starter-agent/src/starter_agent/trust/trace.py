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
        merged = context.merge(
            session_id=self._string(payload.get("session_id")),
            turn_id=self._string(payload.get("turn_id")),
            tool_call_id=self._string(payload.get("call_id") or payload.get("tool_call_id")),
            model_request_id=self._string(payload.get("model_request_id")),
            policy_decision_id=self._string(payload.get("policy_decision_id")),
            approval_id=self._string(payload.get("approval_id") or payload.get("confirmation_id")),
            child_run_id=self._string(payload.get("child_run_id")),
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
