from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Awaitable, Callable, Literal
from urllib.parse import urlsplit
from uuid import uuid4

from starter_agent.capabilities.gate import (
    GateDecision,
    PreToolCallGate,
    ToolCallRequest,
    ToolExecutionDenied,
    _confirmation_allowlist_rule_id,
    _policy_revision,
)
from starter_agent.capabilities.models import (
    AuditEvent,
    Confirmation,
    ConfirmationDecision,
    PolicyRule,
)
from starter_agent.capabilities.policy import classify_tool
from starter_agent.capabilities.store import (
    CapabilityStore,
    RecordAlreadyExistsError,
    RecordNotFoundError,
)


class ConfirmationStateError(RuntimeError):
    pass


class ConfirmationWaitTimeout(TimeoutError):
    pass


class ConfirmationBroker:
    """In-process wake-up channel; SQLite remains the source of truth."""

    def __init__(self) -> None:
        self._waiters: dict[str, asyncio.Future[Confirmation]] = {}
        self._resolved: dict[str, Confirmation] = {}
        self._completed: set[str] = set()

    def register(self, confirmation_id: str) -> None:
        if confirmation_id in self._waiters or confirmation_id in self._resolved:
            return
        self._waiters[confirmation_id] = asyncio.get_running_loop().create_future()

    async def wait(
        self,
        confirmation_id: str,
        *,
        timeout: float,
    ) -> Confirmation:
        resolved = self._resolved.pop(confirmation_id, None)
        if resolved is not None:
            return resolved
        future = self._waiters.get(confirmation_id)
        if future is None:
            future = asyncio.get_running_loop().create_future()
            self._waiters[confirmation_id] = future
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
        except TimeoutError as exc:
            self._waiters.pop(confirmation_id, None)
            future.cancel()
            raise ConfirmationWaitTimeout(confirmation_id) from exc
        finally:
            if future.done() or future.cancelled():
                self._waiters.pop(confirmation_id, None)

    def resolve(self, confirmation: Confirmation) -> bool:
        if confirmation.id in self._completed:
            return False
        future = self._waiters.get(confirmation.id)
        if future is not None:
            if future.done():
                return False
            future.set_result(confirmation)
            self._completed.add(confirmation.id)
            return True
        if confirmation.id in self._resolved:
            return False
        self._resolved[confirmation.id] = confirmation
        self._completed.add(confirmation.id)
        return True

    def has_waiter(self, confirmation_id: str) -> bool:
        future = self._waiters.get(confirmation_id)
        return future is not None and not future.done()


class ConfirmationService:
    def __init__(
        self,
        store: CapabilityStore,
        gate: PreToolCallGate,
        *,
        broker: ConfirmationBroker | None = None,
        confirmation_ttl_seconds: float = 300,
        now: Callable[[], datetime] | None = None,
        expire_orphans: bool = False,
    ) -> None:
        if confirmation_ttl_seconds <= 0 or confirmation_ttl_seconds > 3_600:
            raise ValueError("confirmation_ttl_seconds must be within (0, 3600]")
        self.store = store
        self.gate = gate
        self.broker = broker or ConfirmationBroker()
        self.confirmation_ttl_seconds = confirmation_ttl_seconds
        self._now = now or (lambda: datetime.now(UTC))
        if expire_orphans:
            for confirmation in self.store.expire_pending_confirmations(
                now=self._now(), include_unexpired=True
            ):
                self._audit(
                    confirmation,
                    action="confirmation.expired",
                    decision="deny",
                    reason_code="process_restarted",
                    actor="system",
                )

    def create_pending(
        self,
        request: ToolCallRequest,
        gate_decision: GateDecision,
    ) -> Confirmation:
        if gate_decision.outcome != "require_confirmation":
            raise ConfirmationStateError("confirmation_not_required")
        canonical = self.gate.canonicalize(request)
        for existing in self.store.list_confirmations(
            session_id=canonical.session_id, status="pending"
        ):
            if (
                existing.turn_id == canonical.turn_id
                and existing.call_id == canonical.call_id
                and existing.request_hash == canonical.confirmation_request_hash
            ):
                self.broker.register(existing.id)
                return existing
        rules = self.store.list_policy_rules(canonical.server_id, canonical.tool_name)
        capability = (
            None
            if self.gate.registry is None
            else self.gate.registry.resolve_execution(canonical.tool_name)
        )
        risk = getattr(capability, "risk_level", "external")
        confirmation = Confirmation(
            id=f"confirmation-{uuid4().hex}",
            principal=canonical.principal,
            session_id=canonical.session_id,
            turn_id=canonical.turn_id,
            call_id=canonical.call_id,
            request_hash=canonical.confirmation_request_hash,
            server_id=canonical.server_id,
            tool_name=canonical.tool_name,
            schema_hash=canonical.schema_hash,
            snapshot_id=canonical.snapshot_id,
            arguments_hash=canonical.confirmation_arguments_hash,
            arguments_summary=dict(gate_decision.arguments_summary),
            risk=risk,
            destination=gate_decision.destination_summary,
            data_classes=canonical.data_classes,
            policy_revision=_policy_revision(rules),
            gate_reason_code=gate_decision.reason_code,
            expires_at=self._now()
            + timedelta(seconds=self.confirmation_ttl_seconds),
        )
        self.store.create_confirmation(confirmation)
        self.broker.register(confirmation.id)
        self._audit(
            confirmation,
            action="confirmation.created",
            decision="require_confirmation",
            reason_code=gate_decision.reason_code,
            actor=canonical.principal,
        )
        return confirmation

    def get(self, confirmation_id: str) -> Confirmation:
        confirmation = self.store.get_confirmation(confirmation_id)
        if confirmation is None:
            raise RecordNotFoundError(f"Confirmation not found: {confirmation_id}")
        return confirmation

    def list_pending(self, *, session_id: str | None) -> list[Confirmation]:
        self._expire_due()
        return self.store.list_confirmations(session_id=session_id, status="pending")

    def decide(
        self,
        confirmation_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
        decision: ConfirmationDecision,
        actor: str | None = None,
    ) -> Confirmation:
        current = self.get(confirmation_id)
        if decision == "allowlist" and current.gate_reason_code == "always_confirm":
            raise ConfirmationStateError("allowlist_forbidden_always_confirm")
        if (
            current.status == "consumed"
            and expected_revision == current.revision - 2
            and current.decision == decision
            and current.idempotency_key_hash
            == hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        ):
            return current
        decided = self.store.decide_confirmation(
            confirmation_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            decision=decision,
        )
        if current.status == "pending" and decided.status != "pending":
            audit_decision: Literal["approved", "cancelled", "deny"] = (
                "cancelled" if decided.status == "cancelled" else
                "deny" if decided.status == "expired" else "approved"
            )
            self._audit(
                decided,
                action=(
                    "confirmation.expired"
                    if decided.status == "expired"
                    else "confirmation.decided"
                ),
                decision=audit_decision,
                reason_code=decided.status,
                actor=actor or current.principal,
            )
            self.broker.resolve(decided)
        return decided

    def invalidate(self, confirmation_id: str, *, reason_code: str) -> Confirmation:
        current = self.get(confirmation_id)
        invalidated = self.store.invalidate_confirmation(
            confirmation_id,
            expected_revision=current.revision,
            now=self._now(),
        )
        self._audit(
            invalidated,
            action="confirmation.invalidated",
            decision="deny",
            reason_code=reason_code,
            actor="system",
        )
        self.broker.resolve(invalidated)
        return invalidated

    def expire(self, confirmation_id: str, *, reason_code: str) -> Confirmation:
        current = self.get(confirmation_id)
        expired = self.store.invalidate_confirmation(
            confirmation_id,
            expected_revision=current.revision,
            status="expired",
            now=self._now(),
        )
        self._audit(
            expired,
            action="confirmation.expired",
            decision="deny",
            reason_code=reason_code,
            actor="system",
        )
        self.broker.resolve(expired)
        return expired

    def _expire_due(self) -> None:
        for confirmation in self.store.expire_pending_confirmations(now=self._now()):
            self._audit(
                confirmation,
                action="confirmation.expired",
                decision="deny",
                reason_code="confirmation_timeout",
                actor="system",
            )
            self.broker.resolve(confirmation)

    def _audit(
        self,
        confirmation: Confirmation,
        *,
        action: str,
        decision: str,
        reason_code: str,
        actor: str,
    ) -> None:
        self.store.append_audit_event(
            AuditEvent(
                event_id=f"audit-{uuid4().hex}",
                actor=actor,
                action=action,
                target=f"confirmation:{confirmation.id}",
                decision=decision,
                reason_code=reason_code,
                session_id=confirmation.session_id,
                turn_id=confirmation.turn_id,
                call_id=confirmation.call_id,
                payload={
                    "server_id": confirmation.server_id,
                    "tool_name": confirmation.tool_name,
                    "request_hash": confirmation.request_hash,
                    "schema_hash": confirmation.schema_hash,
                    "arguments_hash": confirmation.arguments_hash,
                    "policy_revision": confirmation.policy_revision,
                    "status": confirmation.status,
                },
                created_at=self._now(),
            )
        )


EventSink = Callable[[dict], Awaitable[None]]


class TurnCoordinator:
    def __init__(
        self,
        confirmations: ConfirmationService,
        *,
        confirmation_timeout_seconds: float = 300,
    ) -> None:
        self.confirmations = confirmations
        self.confirmation_timeout_seconds = confirmation_timeout_seconds

    async def wait_for_permit(
        self,
        request: ToolCallRequest,
        gate_decision: GateDecision,
        *,
        on_event: EventSink | None = None,
    ) -> GateDecision:
        pending = self.confirmations.create_pending(request, gate_decision)
        if on_event is not None:
            await on_event(
                {
                    "type": "confirmation_required",
                    "confirmation_id": pending.id,
                    "server_id": pending.server_id,
                    "tool_name": pending.tool_name,
                    "arguments_summary": dict(pending.arguments_summary),
                    "risk": pending.risk,
                    "destination": pending.destination,
                    "expires_at": pending.expires_at.isoformat(),
                    "revision": pending.revision,
                    "allowlist_allowed": pending.gate_reason_code != "always_confirm",
                }
            )
        timeout = min(
            self.confirmation_timeout_seconds,
            max(0.001, (pending.expires_at - datetime.now(UTC)).total_seconds()),
        )
        try:
            await self.confirmations.broker.wait(pending.id, timeout=timeout)
        except ConfirmationWaitTimeout as exc:
            current = self.confirmations.get(pending.id)
            if current.status == "pending":
                self.confirmations.expire(
                    pending.id, reason_code="confirmation_timeout"
                )
            raise ToolExecutionDenied("tool_confirmation_timeout") from exc
        except asyncio.CancelledError:
            current = self.confirmations.get(pending.id)
            if current.status == "pending":
                self.confirmations.decide(
                    pending.id,
                    expected_revision=current.revision,
                    idempotency_key=f"server-cancel-{pending.id}",
                    decision="cancel",
                    actor="system",
                )
            raise
        current = self.confirmations.get(pending.id)
        if on_event is not None:
            await on_event(
                {
                    "type": "confirmation_resolved",
                    "confirmation_id": current.id,
                    "status": current.status,
                    "decision": current.decision,
                }
            )
        if current.status != "approved":
            raise ToolExecutionDenied(
                "tool_confirmation_cancelled"
                if current.status == "cancelled"
                else f"tool_confirmation_{current.status}"
            )
        rules = self.confirmations.store.list_policy_rules(
            request.server_id, request.tool_name
        )
        if current.policy_revision != _policy_revision(rules):
            self.confirmations.invalidate(
                current.id, reason_code="confirmation_policy_changed"
            )
            raise ToolExecutionDenied("confirmation_policy_changed")
        latest = await self.confirmations.gate.evaluate(request, issue_permit=False)
        if latest.outcome == "deny":
            self.confirmations.invalidate(current.id, reason_code=latest.reason_code)
            raise ToolExecutionDenied(latest.reason_code)
        if current.decision == "allowlist":
            if latest.reason_code == "always_confirm":
                self.confirmations.invalidate(
                    current.id, reason_code="allowlist_forbidden_always_confirm"
                )
                raise ToolExecutionDenied("allowlist_forbidden_always_confirm")
            self._create_allowlist(request, current)
        approved = await self.confirmations.gate.evaluate_approved(
            request, confirmation_id=current.id
        )
        if approved.outcome != "allow" or approved.permit is None:
            self.confirmations.invalidate(
                current.id, reason_code=approved.reason_code
            )
            raise ToolExecutionDenied(approved.reason_code)
        return approved

    def _create_allowlist(
        self, request: ToolCallRequest, confirmation: Confirmation
    ) -> None:
        urls = [
            value
            for key, value in request.arguments.items()
            if "url" in key.casefold() and isinstance(value, str)
        ]
        parsed = [urlsplit(value) for value in urls]
        capability = (
            None
            if self.confirmations.gate.registry is None
            else self.confirmations.gate.registry.resolve_execution(request.tool_name)
        )
        action = (
            classify_tool(capability.metadata, capability.risk_level)
            if capability is not None
            else "read"
        )
        rule = PolicyRule(
            id=_confirmation_allowlist_rule_id(confirmation.id),
            server_id=request.server_id,
            tool_name=request.tool_name,
            effect="allowlist_auto",
            schemes=tuple(sorted({item.scheme for item in parsed if item.scheme})),
            domains=tuple(sorted({item.hostname for item in parsed if item.hostname})),
            actions=(action,),
            data_classes=confirmation.data_classes or (),
            roles=(request.role,),
            schema_hash=request.schema_hash,
            created_by=confirmation.principal,
        )
        try:
            self.confirmations.store.create_policy_rule(rule)
        except RecordAlreadyExistsError:
            existing = self.confirmations.store.get_policy_rule(rule.id)
            if existing != rule:
                raise ConfirmationStateError("allowlist_rule_conflict")
