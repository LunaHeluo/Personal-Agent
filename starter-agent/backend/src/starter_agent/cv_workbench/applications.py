"""User-confirmed application tracking with append-only status events."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

from starter_agent.cv_workbench.contracts import (
    Application,
    ApplicationEvent,
    ApplicationStatus,
    BusinessOperation,
    JobSnapshot,
    OperationStatus,
    ResumeVersion,
    ResumeVersionStatus,
    assert_transition,
)
from starter_agent.cv_workbench.operations import (
    BusinessOperationService,
    CommitReceipt,
    OperationCommand,
    RunBinding,
    RunOutcome,
    SafetyDecision,
    ValidationDecision,
)
from starter_agent.cv_workbench.store import (
    IdempotencyConflictError,
    ObjectNotFoundError,
    SQLiteWorkbenchStore,
)


class ApplicationServiceError(RuntimeError):
    code = "application_service_error"


class ApplicationConfirmationRequiredError(ApplicationServiceError):
    code = "application_confirmation_required"


@dataclass(frozen=True)
class CreateApplicationCommand:
    operation_id: str
    idempotency_key: str
    application_id: str
    event_id: str
    workspace_id: str
    job_snapshot_id: str
    resume_version_id: str
    initial_status: ApplicationStatus = ApplicationStatus.TO_DECIDE
    priority: int = 0
    next_action: str | None = None
    remind_at: datetime | None = None
    note: str | None = None
    user_confirmed: bool = False


@dataclass(frozen=True)
class ApplicationEventCommand:
    operation_id: str
    idempotency_key: str
    event_id: str
    workspace_id: str
    expected_revision: int
    to_status: ApplicationStatus
    note: str | None = None
    next_action: str | None = None
    remind_at: datetime | None = None
    user_confirmed: bool = False


@dataclass(frozen=True)
class ApplicationDetailsCommand:
    expected_revision: int
    priority: int
    next_action: str | None = None
    remind_at: datetime | None = None


@dataclass(frozen=True)
class ApplicationResult:
    operation: BusinessOperation
    application: Application | None


def _canonical_hash(payload: dict[str, object]) -> str:
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _allowed(status: ApplicationStatus) -> tuple[str, ...]:
    values = {
        ApplicationStatus.TO_DECIDE: ("mark_to_apply", "archive"),
        ApplicationStatus.TO_APPLY: ("mark_applied", "withdraw", "archive"),
        ApplicationStatus.APPLIED: ("mark_assessment", "mark_interview", "reject", "withdraw", "archive"),
        ApplicationStatus.ASSESSMENT: ("mark_interview", "reject", "withdraw"),
        ApplicationStatus.INTERVIEW: ("mark_offer", "reject", "withdraw"),
        ApplicationStatus.OFFER: ("withdraw", "archive"),
        ApplicationStatus.REJECTED: ("archive",),
        ApplicationStatus.WITHDRAWN: ("archive",),
        ApplicationStatus.ARCHIVED: (),
    }
    return values[status]


class ApplicationService:
    def __init__(self, *, store: SQLiteWorkbenchStore, clock=lambda: datetime.now(UTC)) -> None:
        self.store = store
        self.clock = clock

    def create(self, command: CreateApplicationCommand, *, principal: str) -> ApplicationResult:
        if not command.user_confirmed:
            raise ApplicationConfirmationRequiredError("application_confirmation_required")
        if command.initial_status not in {ApplicationStatus.TO_DECIDE, ApplicationStatus.TO_APPLY, ApplicationStatus.APPLIED}:
            raise ApplicationServiceError("invalid_initial_application_status")
        self.store.assert_entity_in_workspace(command.job_snapshot_id, command.workspace_id, principal=principal)
        self.store.assert_entity_in_workspace(command.resume_version_id, command.workspace_id, principal=principal)
        version = self.store.get(ResumeVersion, command.resume_version_id, principal=principal)
        if version.status != ResumeVersionStatus.CONFIRMED:
            raise ApplicationServiceError("application_requires_confirmed_version")
        self.store.get(JobSnapshot, command.job_snapshot_id, principal=principal)
        payload = {
            "kind": "create", "application_id": command.application_id,
            "event_id": command.event_id, "workspace_id": command.workspace_id,
            "job_snapshot_id": command.job_snapshot_id, "resume_version_id": command.resume_version_id,
            "initial_status": command.initial_status.value, "priority": command.priority,
            "next_action": command.next_action, "remind_at": command.remind_at,
            "note": command.note,
        }
        return self._execute(command.operation_id, command.idempotency_key, command.workspace_id, payload, _ApplicationCommitter(self, command, principal), principal)

    def append_status(self, application_id: str, command: ApplicationEventCommand, *, principal: str) -> ApplicationResult:
        if not command.user_confirmed:
            raise ApplicationConfirmationRequiredError("application_confirmation_required")
        self.store.assert_entity_in_workspace(application_id, command.workspace_id, principal=principal)
        current = self.store.get(Application, application_id, principal=principal)
        existing = next((item for item in current.events if item.event_id == command.event_id), None)
        if existing is None:
            assert_transition(current.current_status, command.to_status)
        source_status = existing.from_status if existing is not None else current.current_status
        payload = {
            "kind": "event", "application_id": application_id, "event_id": command.event_id,
            "workspace_id": command.workspace_id, "expected_revision": command.expected_revision,
            "from_status": source_status.value if source_status is not None else None,
            "to_status": command.to_status.value,
            "note": command.note, "next_action": command.next_action, "remind_at": command.remind_at,
        }
        return self._execute(command.operation_id, command.idempotency_key, command.workspace_id, payload, _ApplicationCommitter(self, command, principal, application_id), principal)

    def update_details(self, application_id: str, command: ApplicationDetailsCommand, *, principal: str) -> Application:
        current = self.store.get(Application, application_id, principal=principal)
        updated = Application.model_validate(current.model_dump() | {
            "priority": command.priority,
            "next_action": command.next_action,
            "remind_at": command.remind_at,
            "revision": current.revision + 1,
            "updated_at": self.clock(),
        })
        return self.store.update(updated, principal=principal, expected_revision=command.expected_revision)

    def list(self, workspace_id: str, *, principal: str, status: ApplicationStatus | None = None, query: str | None = None) -> tuple[dict[str, object], ...]:
        needle = (query or "").strip().casefold()
        values: list[dict[str, object]] = []
        cursor = None
        while True:
            page = self.store.list(Application, principal=principal, cursor=cursor)
            for item in page.items:
                if item.workspace_id != workspace_id or (status is not None and item.current_status != status):
                    continue
                snapshot = self.store.get(JobSnapshot, item.job_snapshot_id, principal=principal)
                if needle and needle not in " ".join((snapshot.title, snapshot.company, snapshot.location or "", item.next_action or "")).casefold():
                    continue
                values.append({"application": item, "job_snapshot": snapshot})
            if page.next_cursor is None: break
            cursor = page.next_cursor
        return tuple(sorted(values, key=lambda value: (value["application"].priority, value["application"].updated_at), reverse=True))

    def _execute(self, operation_id: str, idempotency_key: str, workspace_id: str, payload: dict[str, object], committer, principal: str) -> ApplicationResult:
        digest = _canonical_hash(payload)
        operations = BusinessOperationService(store=self.store, validator=_LocalValidator(digest), safety_gate=_ConfirmedSafetyGate(), committer=committer, clock=self.clock)
        operation, _ = operations.create(OperationCommand(operation_id, workspace_id, "application_command", idempotency_key, digest), principal=principal)
        if operation.status == OperationStatus.COMMITTED:
            return ApplicationResult(operation, self.store.get(Application, str(operation.result_object_id), principal=principal))
        if operation.status == OperationStatus.COMMIT_FAILED:
            operation = operations.retry_commit(operation.operation_id, principal=principal)
        elif operation.status == OperationStatus.CREATED:
            run_id = f"local-application:{operation.operation_id}"
            operations.bind_run(operation.operation_id, RunBinding(run_id), principal=principal)
            operation = operations.process_run_outcome(operation.operation_id, RunOutcome(run_id, "succeeded", f"application-command://{digest}", digest), principal=principal)
        application = self.store.get(Application, str(operation.result_object_id), principal=principal) if operation.status == OperationStatus.COMMITTED else None
        return ApplicationResult(operation, application)


class _LocalValidator:
    def __init__(self, digest): self.digest = digest
    def validate(self, operation, outcome):
        accepted = outcome.result_sha256 == self.digest and outcome.result_ref == f"application-command://{self.digest}"
        return ValidationDecision(accepted, "application-command-v1", outcome.result_ref, outcome.result_sha256, (), False, None if accepted else "application_command_mismatch")


class _ConfirmedSafetyGate:
    def evaluate(self, operation, decision):
        return SafetyDecision(True, {"user_confirmation_checked": True, "external_submission": False})


class _ApplicationCommitter:
    def __init__(self, service: ApplicationService, command, principal: str, application_id: str | None = None) -> None:
        self.service = service; self.command = command; self.principal = principal
        self.application_id = application_id or command.application_id

    def commit(self, operation, checkpoint):
        if isinstance(self.command, CreateApplicationCommand):
            application = self._create()
        else:
            application = self._append()
        return CommitReceipt(application.application_id)

    def _create(self) -> Application:
        try:
            existing = self.service.store.get(Application, self.application_id, principal=self.principal)
        except ObjectNotFoundError:
            existing = None
        statuses = [ApplicationStatus.TO_DECIDE]
        if self.command.initial_status in {ApplicationStatus.TO_APPLY, ApplicationStatus.APPLIED}: statuses.append(ApplicationStatus.TO_APPLY)
        if self.command.initial_status == ApplicationStatus.APPLIED: statuses.append(ApplicationStatus.APPLIED)
        now = self.service.clock(); events = []
        for index, status in enumerate(statuses):
            intermediate_id = f"ae_{sha256(f'{self.command.event_id}:{status.value}'.encode()).hexdigest()[:24]}"
            events.append(ApplicationEvent(
                event_id=self.command.event_id if index == len(statuses) - 1 else intermediate_id,
                idempotency_key=self.command.idempotency_key if index == len(statuses) - 1 else f"initial:{intermediate_id}",
                from_status=statuses[index - 1] if index else None,
                to_status=status,
                confirmed_by=self.principal,
                occurred_at=now,
                note=self.command.note if index == len(statuses) - 1 else "用户确认创建投递记录",
            ))
        candidate = Application(
            application_id=self.application_id, workspace_id=self.command.workspace_id,
            job_snapshot_id=self.command.job_snapshot_id, resume_version_id=self.command.resume_version_id,
            current_status=self.command.initial_status, priority=self.command.priority,
            next_action=self.command.next_action, remind_at=self.command.remind_at,
            events=tuple(events), revision=1, created_at=now, updated_at=now,
            allowed_actions=_allowed(self.command.initial_status),
        )
        if existing is not None:
            if existing.model_dump(exclude={"created_at", "updated_at"}) != candidate.model_dump(exclude={"created_at", "updated_at"}):
                raise IdempotencyConflictError(self.command.idempotency_key)
            return existing
        return self.service.store.create(candidate, principal=self.principal, workspace_id=self.command.workspace_id)

    def _append(self) -> Application:
        current = self.service.store.get(Application, self.application_id, principal=self.principal)
        existing = next((item for item in current.events if item.event_id == self.command.event_id), None)
        if existing is not None:
            if existing.idempotency_key != self.command.idempotency_key or existing.to_status != self.command.to_status or existing.note != self.command.note:
                raise IdempotencyConflictError(self.command.idempotency_key)
            return current
        if current.revision != self.command.expected_revision:
            from starter_agent.cv_workbench.store import RevisionConflictError
            raise RevisionConflictError(current.application_id, current.revision)
        assert_transition(current.current_status, self.command.to_status)
        event = ApplicationEvent(
            event_id=self.command.event_id, idempotency_key=self.command.idempotency_key,
            from_status=current.current_status, to_status=self.command.to_status,
            confirmed_by=self.principal, occurred_at=self.service.clock(), note=self.command.note,
        )
        updated = Application.model_validate(current.model_dump() | {
            "current_status": self.command.to_status,
            "next_action": self.command.next_action,
            "remind_at": self.command.remind_at,
            "events": current.events + (event,),
            "revision": current.revision + 1,
            "updated_at": event.occurred_at,
            "allowed_actions": _allowed(self.command.to_status),
        })
        return self.service.store.update(updated, principal=self.principal, expected_revision=current.revision)
