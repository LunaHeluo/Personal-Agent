"""Recoverable BusinessOperation orchestration for the CV workbench.

The service coordinates existing Run execution with validation and an
idempotent business committer. It never treats a successful Run as a committed
workbench result and stores only result references/hashes in its checkpoint.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from starter_agent.cv_workbench.contracts import (
    BusinessOperation,
    CONTRACT_VERSION,
    OperationStatus,
    assert_transition,
)
from starter_agent.cv_workbench.store import (
    OperationCheckpoint,
    SQLiteWorkbenchStore,
)


class OperationServiceError(RuntimeError):
    code = "operation_service_error"


class OperationStateError(OperationServiceError):
    code = "operation_state_invalid"


class OperationValidationError(OperationServiceError):
    code = "validation_failed"


class OperationCommitError(OperationServiceError):
    code = "business_commit_failed"


RunOutcomeStatus = Literal[
    "succeeded",
    "partial",
    "failed",
    "cancelled",
    "timed_out",
    "budget_exhausted",
]


@dataclass(frozen=True)
class OperationCommand:
    operation_id: str
    workspace_id: str
    operation_type: str
    idempotency_key: str
    input_sha256: str
    expected_revision: int | None = None


@dataclass(frozen=True)
class RunBinding:
    parent_run_id: str
    task_id: str | None = None


@dataclass(frozen=True)
class RunOutcome:
    parent_run_id: str
    status: RunOutcomeStatus
    result_ref: str | None = None
    result_sha256: str | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class ValidationDecision:
    accepted: bool
    validator_version: str
    result_ref: str | None = None
    result_sha256: str | None = None
    evidence_refs: tuple[str, ...] = ()
    partial: bool = False
    error_code: str | None = None


@dataclass(frozen=True)
class SafetyDecision:
    allowed: bool
    summary: dict[str, Any]
    error_code: str | None = None


@dataclass(frozen=True)
class CommitReceipt:
    result_object_id: str


class ResultValidator(Protocol):
    def validate(
        self, operation: BusinessOperation, outcome: RunOutcome
    ) -> ValidationDecision: ...


class EvidenceSafetyGate(Protocol):
    def evaluate(
        self, operation: BusinessOperation, decision: ValidationDecision
    ) -> SafetyDecision: ...


class BusinessCommitter(Protocol):
    def commit(
        self, operation: BusinessOperation, checkpoint: OperationCheckpoint
    ) -> CommitReceipt:
        """Commit idempotently using operation.operation_id as the command key."""


class RunController(Protocol):
    def cancel(self, parent_run_id: str, *, principal: str) -> None: ...


class BusinessOperationService:
    def __init__(
        self,
        *,
        store: SQLiteWorkbenchStore,
        validator: ResultValidator,
        safety_gate: EvidenceSafetyGate,
        committer: BusinessCommitter,
        run_controller: RunController | None = None,
        clock=lambda: datetime.now(UTC),
    ) -> None:
        self.store = store
        self.validator = validator
        self.safety_gate = safety_gate
        self.committer = committer
        self.run_controller = run_controller
        self.clock = clock

    def create(
        self, command: OperationCommand, *, principal: str
    ) -> tuple[BusinessOperation, bool]:
        now = self.clock()
        operation = BusinessOperation.model_validate(
            {
                "contract_version": CONTRACT_VERSION,
                "operation_id": command.operation_id,
                "workspace_id": command.workspace_id,
                "operation_type": command.operation_type,
                "idempotency_key": command.idempotency_key,
                "input_sha256": command.input_sha256,
                "expected_revision": command.expected_revision,
                "status": OperationStatus.CREATED,
                "parent_run_id": None,
                "task_id": None,
                "result_object_id": None,
                "error_code": None,
                "retryable": False,
                "revision": 1,
                "created_at": now,
                "updated_at": now,
            }
        )
        stored, created = self.store.create_or_get_operation(
            operation, principal=principal
        )
        if created:
            self._audit(
                stored,
                principal,
                "operation_created",
                {"operation_type": stored.operation_type},
            )
        return stored, created

    def bind_run(
        self,
        operation_id: str,
        binding: RunBinding,
        *,
        principal: str,
    ) -> BusinessOperation:
        operation = self.get(operation_id, principal=principal)
        if operation.status == OperationStatus.RUNNING:
            if (
                operation.parent_run_id == binding.parent_run_id
                and operation.task_id == binding.task_id
            ):
                return operation
            raise OperationStateError("operation_already_bound_to_different_run")
        if operation.status != OperationStatus.CREATED:
            raise OperationStateError(f"cannot_bind_run_from:{operation.status}")
        updated = self._transition(
            operation,
            OperationStatus.RUNNING,
            parent_run_id=binding.parent_run_id,
            task_id=binding.task_id,
        )
        stored = self.store.update(
            updated, principal=principal, expected_revision=operation.revision
        )
        self._audit(
            stored,
            principal,
            "run_bound",
            {"parent_run_id": binding.parent_run_id, "task_id": binding.task_id},
        )
        return stored

    def process_run_outcome(
        self,
        operation_id: str,
        outcome: RunOutcome,
        *,
        principal: str,
    ) -> BusinessOperation:
        operation = self.get(operation_id, principal=principal)
        if operation.parent_run_id != outcome.parent_run_id:
            raise OperationStateError("run_outcome_binding_mismatch")
        if operation.status == OperationStatus.COMMITTED:
            return operation
        if operation.status != OperationStatus.RUNNING:
            raise OperationStateError(f"cannot_process_outcome_from:{operation.status}")

        if outcome.status in {
            "failed",
            "timed_out",
            "budget_exhausted",
        }:
            code = outcome.error_code or {
                "failed": "run_failed",
                "timed_out": "run_timed_out",
                "budget_exhausted": "budget_exhausted",
            }[outcome.status]
            failed = self._transition(
                operation,
                OperationStatus.FAILED,
                error_code=code,
                retryable=False,
            )
            stored = self.store.update(
                failed, principal=principal, expected_revision=operation.revision
            )
            self._audit(stored, principal, "run_failed", {"error_code": code})
            return stored
        if outcome.status == "cancelled":
            cancelled = self._transition(operation, OperationStatus.CANCELLED)
            stored = self.store.update(
                cancelled, principal=principal, expected_revision=operation.revision
            )
            self._audit(stored, principal, "run_cancelled", {})
            return stored

        if outcome.result_ref is None or outcome.result_sha256 is None:
            validating = self._transition(operation, OperationStatus.VALIDATING)
            operation = self.store.update(
                validating,
                principal=principal,
                expected_revision=operation.revision,
            )
            self._audit(operation, principal, "validation_started", {})
            return self._reject(
                operation,
                principal=principal,
                error_code="run_result_reference_missing",
            )

        if outcome.status == "partial":
            partial = self._transition(operation, OperationStatus.PARTIAL)
            operation = self.store.update(
                partial, principal=principal, expected_revision=operation.revision
            )
            self._audit(operation, principal, "run_partial", {})

        validating = self._transition(operation, OperationStatus.VALIDATING)
        operation = self.store.update(
            validating, principal=principal, expected_revision=operation.revision
        )
        self._audit(operation, principal, "validation_started", {})

        try:
            decision = self.validator.validate(operation, outcome)
        except Exception as exc:
            return self._reject(
                operation,
                principal=principal,
                error_code=getattr(exc, "code", "validation_failed"),
            )
        if not decision.accepted:
            return self._reject(
                operation,
                principal=principal,
                error_code=decision.error_code or "validation_failed",
            )
        if (
            decision.result_ref != outcome.result_ref
            or decision.result_sha256 != outcome.result_sha256
        ):
            return self._reject(
                operation,
                principal=principal,
                error_code="validation_result_mismatch",
            )
        if outcome.status == "partial" and not decision.partial:
            return self._reject(
                operation,
                principal=principal,
                error_code="partial_result_not_declared",
            )

        try:
            safety = self.safety_gate.evaluate(operation, decision)
        except Exception as exc:
            return self._reject(
                operation,
                principal=principal,
                error_code=getattr(exc, "code", "safety_gate_failed"),
            )
        if not safety.allowed:
            return self._reject(
                operation,
                principal=principal,
                error_code=safety.error_code or "safety_gate_rejected",
            )
        checkpoint = self.store.save_operation_checkpoint(
            operation.operation_id,
            principal=principal,
            result_ref=decision.result_ref,
            result_sha256=decision.result_sha256,
            validator_version=decision.validator_version,
            evidence_refs=decision.evidence_refs,
            safety_summary=safety.summary,
            partial=decision.partial,
        )
        self._audit(
            operation,
            principal,
            "validation_passed",
            {
                "validator_version": decision.validator_version,
                "partial": decision.partial,
            },
        )
        committing = self._transition(operation, OperationStatus.COMMITTING)
        operation = self.store.update(
            committing, principal=principal, expected_revision=operation.revision
        )
        return self._attempt_commit(operation, checkpoint, principal=principal)

    def retry_commit(
        self, operation_id: str, *, principal: str
    ) -> BusinessOperation:
        operation = self.get(operation_id, principal=principal)
        if operation.status == OperationStatus.COMMITTED:
            return operation
        if operation.status != OperationStatus.COMMIT_FAILED:
            raise OperationStateError(f"cannot_retry_commit_from:{operation.status}")
        checkpoint = self.store.get_operation_checkpoint(
            operation_id, principal=principal
        )
        if checkpoint is None:
            raise OperationStateError("commit_checkpoint_missing")
        committing = self._transition(
            operation,
            OperationStatus.COMMITTING,
            error_code=None,
            retryable=False,
        )
        operation = self.store.update(
            committing, principal=principal, expected_revision=operation.revision
        )
        self._audit(operation, principal, "commit_retry_started", {})
        return self._attempt_commit(operation, checkpoint, principal=principal)

    def cancel(self, operation_id: str, *, principal: str) -> BusinessOperation:
        operation = self.get(operation_id, principal=principal)
        if operation.status == OperationStatus.CANCELLED:
            return operation
        if operation.status not in {
            OperationStatus.CREATED,
            OperationStatus.RUNNING,
            OperationStatus.WAITING_FOR_USER,
        }:
            raise OperationStateError(f"cannot_cancel_from:{operation.status}")
        if operation.parent_run_id is not None:
            if self.run_controller is None:
                raise OperationStateError("run_controller_unavailable")
            self.run_controller.cancel(operation.parent_run_id, principal=principal)
        cancelled = self._transition(operation, OperationStatus.CANCELLED)
        stored = self.store.update(
            cancelled, principal=principal, expected_revision=operation.revision
        )
        self._audit(stored, principal, "operation_cancelled", {})
        return stored

    def get(self, operation_id: str, *, principal: str) -> BusinessOperation:
        return self.store.get(
            BusinessOperation, operation_id, principal=principal
        )

    def _attempt_commit(
        self,
        operation: BusinessOperation,
        checkpoint: OperationCheckpoint,
        *,
        principal: str,
    ) -> BusinessOperation:
        try:
            receipt = self.committer.commit(operation, checkpoint)
        except Exception as exc:
            error_code = getattr(exc, "code", "business_commit_failed")
            self.store.record_commit_attempt(
                operation.operation_id,
                principal=principal,
                error=str(error_code),
            )
            failed = self._transition(
                operation,
                OperationStatus.COMMIT_FAILED,
                error_code=str(error_code),
                retryable=True,
            )
            stored = self.store.update(
                failed, principal=principal, expected_revision=operation.revision
            )
            self._audit(
                stored,
                principal,
                "business_commit_failed",
                {"error_code": str(error_code)},
            )
            return stored

        self.store.record_commit_attempt(
            operation.operation_id, principal=principal, error=None
        )
        committed = self._transition(
            operation,
            OperationStatus.COMMITTED,
            result_object_id=receipt.result_object_id,
            error_code=None,
            retryable=False,
        )
        stored = self.store.update(
            committed, principal=principal, expected_revision=operation.revision
        )
        self._audit(
            stored,
            principal,
            "business_committed",
            {"result_object_id": receipt.result_object_id},
        )
        return stored

    def _reject(
        self,
        operation: BusinessOperation,
        *,
        principal: str,
        error_code: str,
    ) -> BusinessOperation:
        rejected = self._transition(
            operation,
            OperationStatus.REJECTED,
            error_code=error_code,
            retryable=False,
        )
        stored = self.store.update(
            rejected, principal=principal, expected_revision=operation.revision
        )
        self._audit(
            stored,
            principal,
            "operation_rejected",
            {"error_code": error_code},
        )
        return stored

    def _transition(
        self,
        operation: BusinessOperation,
        status: OperationStatus,
        **updates: Any,
    ) -> BusinessOperation:
        assert_transition(operation.status, status)
        return BusinessOperation.model_validate(
            operation.model_dump()
            | {
                "status": status,
                "revision": operation.revision + 1,
                "updated_at": self.clock(),
                **updates,
            }
        )

    def _audit(
        self,
        operation: BusinessOperation,
        principal: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        self.store.append_event(
            operation.operation_id,
            principal=principal,
            event_type=event_type,
            payload=payload,
            occurred_at=self.clock(),
        )
