from __future__ import annotations

from datetime import UTC, datetime
from typing import Callable, Literal

from pydantic import Field

from starter_agent.capabilities.confirmations import ConfirmationService
from starter_agent.capabilities.gate import (
    GateDecision,
    PreToolCallGate,
    ToolCallRequest,
)
from starter_agent.capabilities.models import Confirmation
from starter_agent.orchestration.models import OrchestrationModel, PendingAction


class PendingActionResume(OrchestrationModel):
    outcome: Literal["wait", "resume", "stop"]
    reason_code: str = Field(min_length=1, max_length=200)
    pending_action: PendingAction
    permit_id: str | None = Field(default=None, min_length=1, max_length=160)
    resume_node: str | None = Field(default=None, min_length=1, max_length=200)


class PendingActionService:
    """Compatibility adapter over the existing Confirmation/Approval Gate."""

    def __init__(
        self,
        confirmations: ConfirmationService,
        gate: PreToolCallGate,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.confirmations = confirmations
        self.gate = gate
        self._now = now or (lambda: datetime.now(UTC))

    def prepare(
        self,
        *,
        parent_run_id: str,
        action_type: str,
        request: ToolCallRequest,
        gate_decision: GateDecision,
        created_at: datetime,
        step_id: str | None = None,
        impact_summary: tuple[str, ...] = (),
        content_diff_ref: str | None = None,
        attachment_refs: tuple[str, ...] = (),
    ) -> PendingAction:
        confirmation = self.confirmations.create_pending(request, gate_decision)
        return self._from_confirmation(
            confirmation,
            parent_run_id=parent_run_id,
            action_type=action_type,
            created_at=created_at,
            step_id=step_id,
            impact_summary=impact_summary,
            content_diff_ref=content_diff_ref,
            attachment_refs=attachment_refs,
        )

    def refresh(self, pending: PendingAction) -> PendingAction:
        if pending.confirmation_id is None:
            return pending
        confirmation = self.confirmations.get(pending.confirmation_id)
        return self._update_from_confirmation(pending, confirmation)

    async def resume(
        self,
        pending: PendingAction,
        *,
        request: ToolCallRequest,
        budget_available: bool,
        resume_node: str = "executor",
    ) -> PendingActionResume:
        if pending.confirmation_id is None:
            return PendingActionResume(
                outcome="stop",
                reason_code="approval_binding_missing",
                pending_action=pending.model_copy(update={"status": "invalidated"}),
            )
        confirmation = self.confirmations.get(pending.confirmation_id)
        if (
            pending.arguments_hash != request.confirmation_arguments_hash
            or confirmation.arguments_hash != request.confirmation_arguments_hash
        ):
            if confirmation.status not in {"invalidated", "expired", "consumed"}:
                confirmation = self.confirmations.invalidate(
                    confirmation.id,
                    reason_code="pending_action_arguments_changed",
                )
            return PendingActionResume(
                outcome="stop",
                reason_code="pending_action_arguments_changed",
                pending_action=self._update_from_confirmation(pending, confirmation),
            )
        if confirmation.expires_at <= self._now() and confirmation.status in {
            "pending",
            "approved",
        }:
            confirmation = self.confirmations.expire(
                confirmation.id,
                reason_code="pending_action_expired",
            )
        current = self._update_from_confirmation(pending, confirmation)
        if confirmation.status == "pending":
            return PendingActionResume(
                outcome="wait",
                reason_code="approval_pending",
                pending_action=current,
            )
        if confirmation.status == "cancelled":
            return PendingActionResume(
                outcome="stop",
                reason_code="approval_rejected",
                pending_action=current,
            )
        if confirmation.status in {"expired", "invalidated"}:
            return PendingActionResume(
                outcome="stop",
                reason_code=f"approval_{confirmation.status}",
                pending_action=current,
            )
        if confirmation.status == "consumed":
            return PendingActionResume(
                outcome="stop",
                reason_code="approval_already_consumed",
                pending_action=current,
            )
        if not budget_available:
            return PendingActionResume(
                outcome="stop",
                reason_code="budget_unavailable",
                pending_action=current,
            )
        gate_decision = await self.gate.evaluate_approved(
            request,
            confirmation_id=confirmation.id,
        )
        if gate_decision.outcome != "allow" or gate_decision.permit is None:
            return PendingActionResume(
                outcome="stop",
                reason_code=gate_decision.reason_code,
                pending_action=current.model_copy(update={"status": "invalidated"}),
            )
        return PendingActionResume(
            outcome="resume",
            reason_code="approval_revalidated",
            pending_action=current,
            permit_id=gate_decision.permit.id,
            resume_node=resume_node,
        )

    @staticmethod
    def _from_confirmation(
        confirmation: Confirmation,
        *,
        parent_run_id: str,
        action_type: str,
        created_at: datetime,
        step_id: str | None,
        impact_summary: tuple[str, ...],
        content_diff_ref: str | None,
        attachment_refs: tuple[str, ...],
    ) -> PendingAction:
        return PendingAction(
            pending_action_id=f"pending:{confirmation.id}",
            parent_run_id=parent_run_id,
            step_id=step_id,
            action_type=action_type,
            tool_name=confirmation.tool_name,
            target_summary=confirmation.destination[:200],
            arguments_hash=confirmation.arguments_hash or confirmation.request_hash,
            content_diff_ref=content_diff_ref,
            attachment_refs=attachment_refs,
            risk_level=_risk(confirmation.risk),
            irreversible=confirmation.risk in {"write", "external", "dangerous"},
            impact_summary=impact_summary,
            confirmation_id=confirmation.id,
            status=_status(confirmation.status),
            principal=confirmation.principal,
            expires_at=confirmation.expires_at,
            policy_revision=str(confirmation.policy_revision or 0),
            gate_decision_id=f"gate:{confirmation.request_hash[:32]}",
            created_at=created_at,
            decided_at=confirmation.decided_at,
            consumed_at=confirmation.consumed_at,
        )

    @staticmethod
    def _update_from_confirmation(
        pending: PendingAction,
        confirmation: Confirmation,
    ) -> PendingAction:
        return pending.model_copy(
            update={
                "status": _status(confirmation.status),
                "decided_at": confirmation.decided_at,
                "consumed_at": confirmation.consumed_at,
            }
        )


def _risk(value: str) -> str:
    return {
        "read": "low",
        "write": "medium",
        "external": "high",
        "dangerous": "critical",
    }[value]


def _status(value: str) -> str:
    return {
        "pending": "pending",
        "approved": "approved",
        "cancelled": "rejected",
        "expired": "expired",
        "invalidated": "invalidated",
        "consumed": "consumed",
    }[value]

