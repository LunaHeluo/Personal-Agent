from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Generic, Iterator, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    and_,
    create_engine,
    event,
    or_,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import StaticPool

from starter_agent.capabilities.models import (
    BoundedJsonObject,
    Identifier,
    UtcDateTime,
    canonical_json_sha256,
)
from starter_agent.delegation.budget import reserve_budget, settle_budget
from starter_agent.delegation.models import (
    BudgetAllocation,
    BudgetUsage,
    ChildRun,
    ChildTask,
    MergeReport,
    ParentRun,
    RunStatus,
    TaskContract,
    transition_run,
)


class RunStoreError(RuntimeError):
    code = "run_store_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class RecordAlreadyExistsError(RunStoreError):
    code = "run_record_already_exists"


class RecordNotFoundError(RunStoreError):
    code = "run_record_not_found"


class RevisionConflictError(RunStoreError):
    code = "run_revision_conflict"


class MergeConflictError(RevisionConflictError):
    code = "merge_conflict"


class IdempotencyPayloadConflictError(RunStoreError):
    code = "idempotency_payload_conflict"


class StoreModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class RunEvent(StoreModel):
    id: Identifier
    parent_run_id: Identifier
    child_run_id: Identifier | None = None
    event_seq: int | None = Field(default=None, ge=1, le=2**63 - 1)
    event_type: str = Field(min_length=1, max_length=160)
    status: str = Field(min_length=1, max_length=80)
    occurred_at: UtcDateTime
    payload: BoundedJsonObject = Field(default_factory=dict)


class ArtifactLink(StoreModel):
    id: Identifier
    parent_run_id: Identifier
    child_run_id: Identifier | None = None
    artifact_ref: str = Field(min_length=1, max_length=500)
    kind: str = Field(min_length=1, max_length=120)
    restricted: bool = True
    principal: str | None = Field(default=None, min_length=1, max_length=200)
    source_url: str | None = Field(default=None, min_length=1, max_length=2_000)
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    chunk_id: str | None = Field(default=None, min_length=1, max_length=500)
    artifact_type: str | None = Field(default=None, min_length=1, max_length=120)
    trace_ref: str | None = Field(default=None, min_length=1, max_length=500)
    knowledge_user_id: str | None = Field(default=None, min_length=1, max_length=200)
    knowledge_project_id: str | None = Field(default=None, min_length=1, max_length=200)
    knowledge_base_id: str | None = Field(default=None, min_length=1, max_length=200)
    document_id: str | None = Field(default=None, min_length=1, max_length=500)
    tool_name: str | None = Field(default=None, min_length=1, max_length=120)
    policy_decision_id: str | None = Field(default=None, min_length=1, max_length=200)
    approval_id: str | None = Field(default=None, min_length=1, max_length=200)
    created_at: UtcDateTime


class OutboxMessage(StoreModel):
    id: Identifier
    topic: str = Field(min_length=1, max_length=160)
    aggregate_id: Identifier
    idempotency_key: Identifier
    payload: BoundedJsonObject
    status: Literal["pending", "delivered", "failed"] = "pending"
    attempts: int = Field(default=0, ge=0, le=2**63 - 1)
    created_at: UtcDateTime
    delivered_at: UtcDateTime | None = None

    @property
    def payload_hash(self) -> str:
        return canonical_json_sha256(
            {
                "topic": self.topic,
                "aggregate_id": self.aggregate_id,
                "payload": self.payload,
            }
        )


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    items: tuple[T, ...]
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class RunTree:
    parent: ParentRun
    child_tasks: tuple[ChildTask, ...]
    child_runs: tuple[ChildRun, ...]
    allocations: tuple[BudgetAllocation, ...]
    events: tuple[RunEvent, ...]
    artifact_links: tuple[ArtifactLink, ...]
    merge_reports: tuple[MergeReport, ...]


@dataclass(frozen=True, slots=True)
class ChildCreationResult:
    parent: ParentRun
    task: ChildTask
    run: ChildRun
    allocations: tuple[BudgetAllocation, ...]


@dataclass(frozen=True, slots=True)
class BudgetSettlementResult:
    parent: ParentRun
    allocations: tuple[BudgetAllocation, ...]


class CoordinatorCheckpoint(StoreModel):
    schema_version: Literal["1"] = "1"
    parent_run_id: Identifier
    parent_version: int = Field(ge=0)
    payload: BoundedJsonObject
    created_at: UtcDateTime


class DelegateBatch(StoreModel):
    parent_run_id: Identifier
    batch_id: Identifier
    model_request_id: Identifier
    response_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    calls: tuple[BoundedJsonObject, ...]
    completed_call_ids: tuple[Identifier, ...] = ()
    receipts: tuple[BoundedJsonObject, ...] = ()
    context_checkpoint: BoundedJsonObject | None = None
    created_at: UtcDateTime


class CandidateMergeWrite(StoreModel):
    """A facts-only candidate staged before a Parent result is committed."""

    id: Identifier
    parent_run_id: Identifier
    candidate_key: Identifier
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload: BoundedJsonObject
    idempotency_key: Identifier
    expected_parent_version: int = Field(ge=0)
    created_at: UtcDateTime


class ValidatedResultAcceptance(StoreModel):
    """Inputs already checked by ResultValidator; rechecked under one DB lease."""

    child_run_id: Identifier
    envelope_ref: str = Field(min_length=1, max_length=500)
    envelope_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    usage: BudgetUsage
    expected_parent_version: int = Field(ge=0)
    expected_task_version: int = Field(ge=0)
    accepted_at: UtcDateTime
    merge_report: MergeReport | None = None
    candidates: tuple[CandidateMergeWrite, ...] = ()


class ParentMergeFinalization(StoreModel):
    """The facts-only final Parent write, protected by one Parent CAS."""

    parent_run_id: Identifier
    expected_parent_version: int = Field(ge=0)
    report: MergeReport
    candidates: tuple[CandidateMergeWrite, ...]
    terminal_status: Literal["succeeded", "partial"]
    occurred_at: UtcDateTime


class ResultRepairAttempt(StoreModel):
    child_run_id: Identifier
    envelope_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    attempt: int = Field(ge=1, le=1)
    error_code: str = Field(min_length=1, max_length=160)
    usage: BudgetUsage | None = None
    expected_parent_version: int = Field(ge=0)
    occurred_at: UtcDateTime


class ResultRepairCompletion(StoreModel):
    child_run_id: Identifier
    envelope_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    usage: BudgetUsage | None = None
    status: str = Field(min_length=1, max_length=80)
    expected_parent_version: int = Field(ge=0)
    occurred_at: UtcDateTime


@dataclass(frozen=True, slots=True)
class ClaimedChild:
    run: ChildRun
    task: ChildTask
    parent: ParentRun
    lease_token: str
    parent_cancellation_version: int

    @property
    def specialist_id(self) -> str:
        return self.task.specialist_id


class DelegationBase(DeclarativeBase):
    pass


class ParentRunRow(DelegationBase):
    __tablename__ = "delegation_parent_runs"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    version: Mapped[int] = mapped_column(Integer)
    next_event_seq: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload_json: Mapped[str] = mapped_column(Text)


class ChildTaskRow(DelegationBase):
    __tablename__ = "delegation_child_tasks"
    __table_args__ = (
        UniqueConstraint(
            "parent_run_id", "idempotency_key", name="uq_parent_task_idempotency"
        ),
    )

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    parent_run_id: Mapped[str] = mapped_column(
        String(160), ForeignKey("delegation_parent_runs.id"), index=True
    )
    specialist_id: Mapped[str] = mapped_column(String(160), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    version: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(160), index=True)
    accepted_child_run_id: Mapped[str | None] = mapped_column(
        String(160), nullable=True, index=True
    )
    contract_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload_json: Mapped[str] = mapped_column(Text)


class ChildRunRow(DelegationBase):
    __tablename__ = "delegation_child_runs"
    __table_args__ = (
        UniqueConstraint("child_task_id", "attempt", name="uq_child_task_attempt"),
    )

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    child_task_id: Mapped[str] = mapped_column(
        String(160), ForeignKey("delegation_child_tasks.id"), index=True
    )
    parent_run_id: Mapped[str] = mapped_column(
        String(160), ForeignKey("delegation_parent_runs.id"), index=True
    )
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40), index=True)
    version: Mapped[int] = mapped_column(Integer)
    lease_owner: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    lease_token: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload_json: Mapped[str] = mapped_column(Text)


class ChildCheckpointRow(DelegationBase):
    __tablename__ = "delegation_child_checkpoints"

    ref: Mapped[str] = mapped_column(String(240), primary_key=True)
    child_run_id: Mapped[str] = mapped_column(String(160), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload_json: Mapped[str] = mapped_column(Text)


class BudgetAllocationRow(DelegationBase):
    __tablename__ = "delegation_budget_allocations"
    __table_args__ = (
        UniqueConstraint("child_run_id", "dimension", name="uq_child_budget_dimension"),
    )

    id: Mapped[str] = mapped_column(String(360), primary_key=True)
    parent_run_id: Mapped[str] = mapped_column(
        String(160), ForeignKey("delegation_parent_runs.id"), index=True
    )
    child_task_id: Mapped[str] = mapped_column(
        String(160), ForeignKey("delegation_child_tasks.id"), index=True
    )
    child_run_id: Mapped[str] = mapped_column(
        String(160), ForeignKey("delegation_child_runs.id"), index=True
    )
    dimension: Mapped[str] = mapped_column(String(40), index=True)
    version: Mapped[int] = mapped_column(Integer)
    payload_json: Mapped[str] = mapped_column(Text)


class RunEventRow(DelegationBase):
    __tablename__ = "delegation_run_events"
    __table_args__ = (
        UniqueConstraint("parent_run_id", "event_seq", name="uq_parent_event_seq"),
    )

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    parent_run_id: Mapped[str] = mapped_column(
        String(160), ForeignKey("delegation_parent_runs.id"), index=True
    )
    child_run_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    event_seq: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(160), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text)


class ArtifactLinkRow(DelegationBase):
    __tablename__ = "delegation_artifact_links"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    parent_run_id: Mapped[str] = mapped_column(
        String(160), ForeignKey("delegation_parent_runs.id"), index=True
    )
    child_run_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    artifact_ref: Mapped[str] = mapped_column(String(500), index=True)
    kind: Mapped[str] = mapped_column(String(120), index=True)
    restricted: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload_json: Mapped[str] = mapped_column(Text)


class MergeReportRow(DelegationBase):
    __tablename__ = "delegation_merge_reports"
    __table_args__ = (
        UniqueConstraint("parent_run_id", "result_version", name="uq_parent_result_version"),
    )

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    parent_run_id: Mapped[str] = mapped_column(
        String(160), ForeignKey("delegation_parent_runs.id"), index=True
    )
    result_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload_json: Mapped[str] = mapped_column(Text)


class OutboxRow(DelegationBase):
    __tablename__ = "delegation_outbox"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    topic: Mapped[str] = mapped_column(String(160), index=True)
    aggregate_id: Mapped[str] = mapped_column(String(160), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(40), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload_json: Mapped[str] = mapped_column(Text)


class CoordinatorCheckpointRow(DelegationBase):
    __tablename__ = "delegation_coordinator_checkpoints"

    parent_run_id: Mapped[str] = mapped_column(
        String(160), ForeignKey("delegation_parent_runs.id"), primary_key=True
    )
    parent_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[str] = mapped_column(Text)


class DelegateBatchRow(DelegationBase):
    __tablename__ = "delegation_delegate_batches"
    batch_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    parent_run_id: Mapped[str] = mapped_column(String(160), ForeignKey("delegation_parent_runs.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    payload_json: Mapped[str] = mapped_column(Text)


class CandidateMergeRow(DelegationBase):
    __tablename__ = "delegation_candidate_merges"
    __table_args__ = (
        UniqueConstraint("parent_run_id", "candidate_key", name="uq_parent_candidate_key"),
        UniqueConstraint("parent_run_id", "idempotency_key", name="uq_parent_candidate_merge_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    parent_run_id: Mapped[str] = mapped_column(String(160), ForeignKey("delegation_parent_runs.id"), index=True)
    candidate_key: Mapped[str] = mapped_column(String(160), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), index=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    parent_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload_json: Mapped[str] = mapped_column(Text)


class ResultRepairRow(DelegationBase):
    __tablename__ = "delegation_result_repairs"
    child_run_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    envelope_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(80), index=True)
    parent_version: Mapped[int] = mapped_column(Integer)
    payload_json: Mapped[str] = mapped_column(Text)


class SQLiteRunStore:
    def __init__(self, database_url: str, project_root: Path | str) -> None:
        root = Path(project_root)
        engine_options: dict[str, Any] = {}
        if database_url == "sqlite:///:memory:":
            engine_options = {
                "poolclass": StaticPool,
                "connect_args": {"check_same_thread": False},
            }
        elif database_url.startswith("sqlite:///"):
            relative = database_url.removeprefix("sqlite:///")
            database_path = Path(relative)
            if not database_path.is_absolute():
                database_path = root / database_path
            database_path.parent.mkdir(parents=True, exist_ok=True)
            database_url = f"sqlite:///{database_path}"
        self.engine = create_engine(database_url, **engine_options)
        if self.engine.dialect.name == "sqlite":

            @event.listens_for(self.engine, "connect")
            def _configure_sqlite(dbapi_connection, _record) -> None:
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.close()

            with self.engine.begin() as connection:
                connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        DelegationBase.metadata.create_all(self.engine)
        self._event_sinks: list[Callable[[RunEvent], None]] = []

    def add_event_sink(self, sink: Callable[[RunEvent], None]) -> None:
        """Observe a committed RunEvent without participating in its transaction."""
        if sink not in self._event_sinks:
            self._event_sinks.append(sink)

    def _emit_committed(self, *events: RunEvent) -> None:
        # Projection is recoverable by event_seq.  Observability must never
        # turn a completed RunStore transaction into a failed business action.
        for event_item in events:
            for sink in tuple(self._event_sinks):
                try:
                    sink(event_item)
                except Exception:
                    continue

    def close(self) -> None:
        self.engine.dispose()

    @contextmanager
    def _write_session(self) -> Iterator[Session]:
        with Session(self.engine, expire_on_commit=False) as db:
            pending_events: list[RunEvent] = []

            @event.listens_for(db, "after_flush")
            def _collect_new_events(session, _flush_context) -> None:
                pending_events.extend(
                    RunEvent.model_validate_json(row.payload_json)
                    for row in session.new
                    if isinstance(row, RunEventRow)
                )

            try:
                if self.engine.dialect.name == "sqlite":
                    db.connection().exec_driver_sql("BEGIN IMMEDIATE")
                yield db
                # Every durable RunEvent is projected after commit, regardless
                # of which state/lease/budget/merge path created it.
                db.commit()
                self._emit_committed(*pending_events)
            except Exception:
                db.rollback()
                raise

    def create_parent(self, parent: ParentRun) -> ParentRun:
        with self._write_session() as db:
            if db.get(ParentRunRow, parent.id) is not None:
                raise RecordAlreadyExistsError(f"Parent run already exists: {parent.id}")
            db.add(self._parent_row(parent))
            try:
                db.flush()
            except IntegrityError as exc:
                raise RecordAlreadyExistsError(
                    f"Parent run already exists: {parent.id}"
                ) from exc
        return parent

    def create_parent_with_event(
        self,
        parent: ParentRun,
        run_event: RunEvent,
    ) -> ParentRun:
        """Atomically persist a Parent and its creation event."""
        if run_event.parent_run_id != parent.id or run_event.child_run_id is not None:
            raise ValueError("parent creation event binding mismatch")
        with self._write_session() as db:
            if db.get(ParentRunRow, parent.id) is not None:
                raise RecordAlreadyExistsError(
                    f"Parent run already exists: {parent.id}"
                )
            db.add(self._parent_row(parent))
            db.flush()
            assigned = self._assign_event_sequence(db, run_event)
            db.add(self._event_row(assigned))
        return parent

    def transition_orchestration_parent(
        self,
        parent_run_id: str,
        *,
        public_status: str,
        phase: str,
        reason_code: str | None,
        expected_version: int,
        occurred_at: datetime,
    ) -> ParentRun:
        """Advance an orchestration Parent and update its task snapshot atomically."""
        emitted: list[RunEvent] = []
        with self._write_session() as db:
            row = self._require_parent_row(db, parent_run_id)
            current = ParentRun.model_validate_json(row.payload_json)
            if current.run_type != "job_application_orchestration":
                raise RunStoreError(
                    "parent is not an orchestration task",
                    code="orchestration_parent_required",
                )
            if current.version != expected_version:
                raise RevisionConflictError(
                    f"Parent {parent_run_id} expected version {expected_version}, "
                    f"found {current.version}"
                )
            internal_target = {
                "queued": "queued",
                "running": "running",
                "waiting": "waiting_children",
                "partial": "partial",
                "completed": "succeeded",
                "failed": "failed",
                "cancelled": "cancelled",
                "interrupted": "failed",
            }.get(public_status)
            if internal_target is None:
                raise RunStoreError(
                    "unknown background task status",
                    code="background_status_invalid",
                )
            updated = current
            transition_pairs: list[tuple[ParentRun, ParentRun]] = []

            def advance(target: str) -> None:
                nonlocal updated
                before = updated
                updated = transition_run(
                    updated,
                    target,
                    expected_version=updated.version,
                    occurred_at=occurred_at,
                )
                transition_pairs.append((before, updated))

            if updated.status == internal_target:
                # Idempotent replay is only valid when the public state agrees.
                pass
            else:
                if updated.status in {"waiting_children", "waiting_for_user"}:
                    advance("queued")
                if internal_target in {"partial", "succeeded", "failed"} and updated.status == "queued":
                    advance("running")
                if internal_target == "cancelled" and updated.status != "cancelling":
                    advance("cancelling")
                if updated.status != internal_target:
                    advance(internal_target)

            state_payload = dict(updated.orchestration_state or {})
            task_payload = dict(state_payload.get("background_task") or {})
            task_payload.update(
                {
                    "status": public_status,
                    "internal_status": updated.status,
                    "reason_code": reason_code,
                    "phase": phase,
                    "updated_at": occurred_at.isoformat(),
                    "version": updated.version + 1,
                }
            )
            if public_status in {
                "partial", "completed", "failed", "cancelled", "interrupted"
            }:
                task_payload["completed_at"] = occurred_at.isoformat()
            elif public_status == "running" and task_payload.get("started_at") is None:
                task_payload["started_at"] = occurred_at.isoformat()
            state_payload.update(
                {
                    "background_task": task_payload,
                    "execution_status": (
                        "completed" if public_status == "completed" else public_status
                    ),
                    "stop_reason": reason_code,
                    "state_version": int(state_payload.get("state_version", 0)) + 1,
                    "updated_at": occurred_at.isoformat(),
                }
            )
            updated = ParentRun.model_validate(
                {
                    **updated.model_dump(mode="python"),
                    "phase": phase,
                    "version": updated.version + 1,
                    "updated_at": occurred_at,
                    "orchestration_state_version": current.orchestration_state_version + 1,
                    "orchestration_state": state_payload,
                    "stop_reason_code": reason_code,
                }
            )
            row.status = updated.status
            row.version = updated.version
            row.updated_at = updated.updated_at
            row.payload_json = updated.model_dump_json()
            for before, after in transition_pairs:
                self._append_status_event(
                    db,
                    current=before,
                    updated=after,
                    occurred_at=occurred_at,
                )
            lifecycle = RunEvent(
                id=f"background-status:{parent_run_id}:{updated.version}",
                parent_run_id=parent_run_id,
                event_type="background_task.status_changed",
                status="completed",
                occurred_at=occurred_at,
                payload={
                    "task_id": updated.task_id,
                    "to": public_status,
                    "reason_code": reason_code,
                    "version": updated.version,
                },
            )
            lifecycle = self._assign_event_sequence(db, lifecycle)
            db.add(self._event_row(lifecycle))
            emitted.append(lifecycle)
        return updated

    def save_coordinator_checkpoint(
        self,
        checkpoint: CoordinatorCheckpoint,
        *,
        expected_parent_version: int,
        suspend: bool,
        phase: str,
    ) -> ParentRun:
        with self._write_session() as db:
            row = self._require_parent_row(db, checkpoint.parent_run_id)
            current = ParentRun.model_validate_json(row.payload_json)
            if current.version != expected_parent_version:
                raise RevisionConflictError(
                    f"Parent {current.id} expected version {expected_parent_version}, found {current.version}"
                )
            if suspend:
                updated = transition_run(current, "waiting_children", expected_version=current.version, occurred_at=checkpoint.created_at)
            else:
                updated = current.model_copy(update={"version": current.version + 1, "updated_at": checkpoint.created_at})
            updated = ParentRun.model_validate({**updated.model_dump(mode="python"), "phase": phase})
            persisted = checkpoint.model_copy(update={"parent_version": updated.version})
            checkpoint_row = db.get(CoordinatorCheckpointRow, checkpoint.parent_run_id)
            if checkpoint_row is None:
                db.add(CoordinatorCheckpointRow(parent_run_id=checkpoint.parent_run_id, parent_version=updated.version, created_at=checkpoint.created_at, payload_json=persisted.model_dump_json()))
            else:
                checkpoint_row.parent_version = updated.version
                checkpoint_row.created_at = checkpoint.created_at
                checkpoint_row.payload_json = persisted.model_dump_json()
            row.status, row.version, row.updated_at = updated.status, updated.version, updated.updated_at
            row.payload_json = updated.model_dump_json()
            if current.status != updated.status:
                self._append_status_event(db, current=current, updated=updated, occurred_at=checkpoint.created_at)
            return updated

    def get_coordinator_checkpoint(self, parent_run_id: str) -> CoordinatorCheckpoint | None:
        with Session(self.engine) as db:
            row = db.get(CoordinatorCheckpointRow, parent_run_id)
            if row is None:
                return None
            try:
                return CoordinatorCheckpoint.model_validate_json(row.payload_json)
            except Exception as exc:
                raise RunStoreError("coordinator checkpoint is invalid", code="coordinator_checkpoint_invalid") from exc

    def save_delegate_batch(self, batch: DelegateBatch) -> DelegateBatch:
        with self._write_session() as db:
            self._require_parent_row(db, batch.parent_run_id)
            row = db.get(DelegateBatchRow, batch.batch_id)
            if row is not None:
                existing = DelegateBatch.model_validate_json(row.payload_json)
                if existing.batch_id != batch.batch_id or existing.calls != batch.calls:
                    raise IdempotencyPayloadConflictError("delegate batch conflict")
                return existing
            active_rows = db.scalars(select(DelegateBatchRow).where(DelegateBatchRow.parent_run_id == batch.parent_run_id, DelegateBatchRow.active.is_(True))).all()
            for active_row in active_rows:
                active_batch = DelegateBatch.model_validate_json(active_row.payload_json)
                if len(active_batch.completed_call_ids) != len(active_batch.calls):
                    raise RunStoreError("active delegate batch is incomplete", code="delegate_batch_active_incomplete")
            for active_row in active_rows:
                active_row.active = False
            db.add(DelegateBatchRow(parent_run_id=batch.parent_run_id, batch_id=batch.batch_id, created_at=batch.created_at, payload_json=batch.model_dump_json()))
            return batch

    def complete_delegate_batch_call(self, parent_run_id: str, call_id: str, receipt: dict[str, Any] | None = None, *, batch_id: str | None = None, context_checkpoint: dict[str, Any] | None = None) -> DelegateBatch:
        with self._write_session() as db:
            row = db.get(DelegateBatchRow, batch_id) if batch_id else db.scalar(select(DelegateBatchRow).where(DelegateBatchRow.parent_run_id == parent_run_id, DelegateBatchRow.active.is_(True)))
            if row is None:
                raise RecordNotFoundError("delegate batch not found")
            current = DelegateBatch.model_validate_json(row.payload_json)
            valid_ids = {str(call["id"]) for call in current.calls}
            if call_id not in valid_ids:
                raise RunStoreError("delegate batch call not found", code="delegate_batch_call_not_found")
            if call_id in current.completed_call_ids:
                existing = next(item for item in current.receipts if item["call_id"] == call_id)
                controlled = receipt or {"ok": False, "error_code": "delegate_result_missing"}
                if existing.get("outcome_hash") != canonical_json_sha256(controlled):
                    raise RunStoreError("delegate batch receipt conflict", code="delegate_batch_receipt_conflict")
                return current
            controlled = receipt or {"ok": False, "error_code": "delegate_result_missing"}
            updated = current.model_copy(update={"completed_call_ids": (*current.completed_call_ids, call_id), "receipts": (*current.receipts, {"call_id": call_id, "outcome": controlled, "outcome_hash": canonical_json_sha256(controlled)}), "context_checkpoint": context_checkpoint or current.context_checkpoint})
            row.payload_json = updated.model_dump_json()
            return updated

    def get_delegate_batch(self, parent_run_id: str, *, batch_id: str | None = None) -> DelegateBatch | None:
        with Session(self.engine) as db:
            row = db.get(DelegateBatchRow, batch_id) if batch_id else db.scalar(select(DelegateBatchRow).where(DelegateBatchRow.parent_run_id == parent_run_id, DelegateBatchRow.active.is_(True)))
            if row is not None and row.parent_run_id != parent_run_id:
                return None
            return None if row is None else DelegateBatch.model_validate_json(row.payload_json)

    def set_parent_phase(
        self,
        parent_run_id: str,
        *,
        phase: str,
        expected_version: int,
        occurred_at: datetime,
        terminal_status: Literal["succeeded", "partial", "failed"] | None = None,
    ) -> ParentRun:
        with self._write_session() as db:
            row = self._require_parent_row(db, parent_run_id)
            current = ParentRun.model_validate_json(row.payload_json)
            if current.version != expected_version:
                raise RevisionConflictError(f"Parent revision conflict: {parent_run_id}")
            if terminal_status is None:
                updated = ParentRun.model_validate({
                    **current.model_dump(mode="python"),
                    "phase": phase,
                    "version": current.version + 1,
                    "updated_at": occurred_at,
                })
            else:
                updated = transition_run(current, terminal_status, expected_version=current.version, occurred_at=occurred_at)
                updated = ParentRun.model_validate({**updated.model_dump(mode="python"), "phase": phase})
            row.status, row.version, row.updated_at = updated.status, updated.version, updated.updated_at
            row.payload_json = updated.model_dump_json()
            if current.status != updated.status:
                self._append_status_event(db, current=current, updated=updated, occurred_at=occurred_at)
            return updated

    def transition_parent_with_phase(
        self,
        parent_run_id: str,
        *,
        target_status: RunStatus,
        phase: str,
        expected_version: int,
        occurred_at: datetime,
    ) -> ParentRun:
        with self._write_session() as db:
            row = self._require_parent_row(db, parent_run_id)
            current = ParentRun.model_validate_json(row.payload_json)
            if current.version != expected_version:
                raise RevisionConflictError(f"Parent revision conflict: {parent_run_id}")
            updated = transition_run(current, target_status, expected_version=current.version, occurred_at=occurred_at)
            updated = ParentRun.model_validate({**updated.model_dump(mode="python"), "phase": phase})
            row.status, row.version, row.updated_at = updated.status, updated.version, updated.updated_at
            row.payload_json = updated.model_dump_json()
            self._append_status_event(db, current=current, updated=updated, occurred_at=occurred_at)
            return updated

    def checkpoint_and_transition_parent(
        self,
        checkpoint: CoordinatorCheckpoint,
        *,
        target_status: RunStatus,
        phase: str,
        expected_version: int,
    ) -> ParentRun:
        with self._write_session() as db:
            row = self._require_parent_row(db, checkpoint.parent_run_id)
            current = ParentRun.model_validate_json(row.payload_json)
            if current.version != expected_version:
                raise RevisionConflictError(f"Parent revision conflict: {current.id}")
            updated = transition_run(current, target_status, expected_version=current.version, occurred_at=checkpoint.created_at)
            updated = ParentRun.model_validate({**updated.model_dump(mode="python"), "phase": phase})
            persisted = checkpoint.model_copy(update={"parent_version": updated.version})
            checkpoint_row = db.get(CoordinatorCheckpointRow, current.id)
            if checkpoint_row is None:
                db.add(CoordinatorCheckpointRow(parent_run_id=current.id, parent_version=updated.version, created_at=checkpoint.created_at, payload_json=persisted.model_dump_json()))
            else:
                checkpoint_row.parent_version, checkpoint_row.created_at, checkpoint_row.payload_json = updated.version, checkpoint.created_at, persisted.model_dump_json()
            row.status, row.version, row.updated_at, row.payload_json = updated.status, updated.version, updated.updated_at, updated.model_dump_json()
            self._append_status_event(db, current=current, updated=updated, occurred_at=checkpoint.created_at)
            return updated

    def resume_parent_for_validation(
        self, parent_run_id: str, *, expected_version: int, occurred_at: datetime,
        idempotency_key: str,
    ) -> ParentRun:
        """Resume a waiting Parent with one durable idempotency receipt."""
        with self._write_session() as db:
            row = self._require_parent_row(db, parent_run_id)
            current = ParentRun.model_validate_json(row.payload_json)
            existing_row = db.scalar(select(RunEventRow).where(
                RunEventRow.parent_run_id == parent_run_id,
                RunEventRow.event_type == "parent.resume_requested",
            ).order_by(RunEventRow.event_seq.desc()))
            existing = (
                None if existing_row is None
                else RunEvent.model_validate_json(existing_row.payload_json)
            )
            if existing is not None and existing.payload.get("idempotency_key") == idempotency_key:
                if existing.payload.get("expected_version") != expected_version:
                    raise IdempotencyPayloadConflictError("resume idempotency key has different payload")
                return current
            if current.version != expected_version:
                raise RevisionConflictError(f"Parent revision conflict: {parent_run_id}")
            target_status = "queued" if current.status == "waiting_for_user" else "running"
            updated = transition_run(
                current,
                target_status,
                expected_version=current.version,
                occurred_at=occurred_at,
            )
            updated = ParentRun.model_validate({
                **updated.model_dump(mode="python"), "phase": "validating",
            })
            row.status, row.version, row.updated_at, row.payload_json = (
                updated.status, updated.version, updated.updated_at, updated.model_dump_json()
            )
            self._append_status_event(db, current=current, updated=updated, occurred_at=occurred_at)
            event = self._assign_event_sequence(db, RunEvent(
                id=f"parent-resume:{parent_run_id}:{updated.version}", parent_run_id=parent_run_id,
                event_type="parent.resume_requested", status="completed", occurred_at=occurred_at,
                payload={"idempotency_key": idempotency_key, "expected_version": expected_version},
            ))
            db.add(self._event_row(event))
            return updated

    def get_parent(self, parent_run_id: str) -> ParentRun | None:
        with Session(self.engine) as db:
            row = db.get(ParentRunRow, parent_run_id)
            return None if row is None else ParentRun.model_validate_json(row.payload_json)

    def get_child_task(self, child_task_id: str) -> ChildTask | None:
        with Session(self.engine) as db:
            row = db.get(ChildTaskRow, child_task_id)
            return None if row is None else ChildTask.model_validate_json(row.payload_json)

    def get_child_run(self, child_run_id: str) -> ChildRun | None:
        with Session(self.engine) as db:
            row = db.get(ChildRunRow, child_run_id)
            return None if row is None else ChildRun.model_validate_json(row.payload_json)

    def list_parents(
        self,
        *,
        limit: int = 100,
        cursor: str | None = None,
    ) -> Page[ParentRun]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with Session(self.engine) as db:
            statement = select(ParentRunRow)
            if cursor is not None:
                cursor_row = db.get(ParentRunRow, cursor)
                if cursor_row is None:
                    raise RecordNotFoundError(f"Parent cursor not found: {cursor}")
                statement = statement.where(
                    or_(
                        ParentRunRow.created_at > cursor_row.created_at,
                        and_(
                            ParentRunRow.created_at == cursor_row.created_at,
                            ParentRunRow.id > cursor_row.id,
                        ),
                    )
                )
            rows = db.scalars(
                statement.order_by(ParentRunRow.created_at, ParentRunRow.id).limit(limit + 1)
            ).all()
            has_more = len(rows) > limit
            selected = rows[:limit]
            items = tuple(ParentRun.model_validate_json(row.payload_json) for row in selected)
            next_cursor = selected[-1].id if has_more and selected else None
            return Page(items=items, next_cursor=next_cursor)

    def queue_depth(self, *, now: datetime) -> int:
        """Count runnable Child Runs without claiming them."""
        with Session(self.engine) as db:
            rows = db.scalars(
                select(ChildRunRow).where(ChildRunRow.status == "queued")
            ).all()
            count = 0
            for row in rows:
                run = ChildRun.model_validate_json(row.payload_json)
                parent_row = db.get(ParentRunRow, run.parent_run_id)
                if parent_row is None:
                    continue
                parent = ParentRun.model_validate_json(parent_row.payload_json)
                if (
                    parent.status not in {"cancelling", "cancelled"}
                    and parent.cancel_requested_at is None
                ):
                    count += 1
            return count

    def claim_next_child_run(
        self,
        *,
        worker_id: str,
        lease_token: str,
        claimed_at: datetime,
        lease_ttl: timedelta,
        excluded_specialists: frozenset[str] | None = None,
    ) -> ClaimedChild | None:
        """Select and claim the fair queue head in one SQLite write transaction."""
        if lease_ttl <= timedelta(0):
            raise ValueError("lease_ttl must be positive")
        with self._write_session() as db:
            candidates = db.execute(
                select(ChildRunRow, ChildTaskRow, ParentRunRow)
                .join(ChildTaskRow, ChildTaskRow.id == ChildRunRow.child_task_id)
                .join(ParentRunRow, ParentRunRow.id == ChildRunRow.parent_run_id)
                .where(ChildRunRow.status == "queued")
                .order_by(
                    ChildRunRow.created_at,
                    ChildRunRow.id,
                )
            ).all()
            eligible: list[tuple[tuple[object, ...], ChildRunRow, ChildTask, ParentRun]] = []
            for run_row, task_row, parent_row in candidates:
                run = ChildRun.model_validate_json(run_row.payload_json)
                task = ChildTask.model_validate_json(task_row.payload_json)
                parent = ParentRun.model_validate_json(parent_row.payload_json)
                if excluded_specialists is not None and task.specialist_id in excluded_specialists:
                    continue
                if (
                    (run.available_at or run.created_at) <= claimed_at < run.deadline_at
                    and parent.available_at <= claimed_at < parent.deadline_at
                    and parent.status not in {"cancelling", "cancelled"}
                    and parent.cancel_requested_at is None
                ):
                    eligible.append(
                        ((parent.priority, run.available_at or parent.available_at, run.created_at, run.id), run_row, task, parent)
                    )
            if not eligible:
                return None
            _, row, task, parent = min(eligible, key=lambda item: item[0])
            current = ChildRun.model_validate_json(row.payload_json)
            claimed = transition_run(
                current, "running", expected_version=current.version, occurred_at=claimed_at
            ).model_copy(update={
                "lease_owner": worker_id,
                "lease_token": lease_token,
                "lease_expires_at": claimed_at + lease_ttl,
                "heartbeat_at": claimed_at,
            })
            claimed = ChildRun.model_validate(claimed.model_dump(mode="python"))
            result = db.execute(
                update(ChildRunRow)
                .where(
                    ChildRunRow.id == current.id,
                    ChildRunRow.status == "queued",
                    ChildRunRow.version == current.version,
                )
                .values(
                    status=claimed.status, version=claimed.version,
                    lease_owner=worker_id, lease_token=lease_token,
                    lease_expires_at=claimed.lease_expires_at,
                    heartbeat_at=claimed.heartbeat_at, updated_at=claimed.updated_at,
                    payload_json=claimed.model_dump_json(),
                )
            )
            if result.rowcount != 1:
                return None
            self._append_status_event(db, current=current, updated=claimed, occurred_at=claimed_at)
            return ClaimedChild(
                run=claimed, task=task, parent=parent, lease_token=lease_token,
                parent_cancellation_version=parent.cancellation_version,
            )

    def request_parent_cancellation(
        self, parent_run_id: str, *, reason: str, requested_at: datetime,
        expected_version: int | None = None, idempotency_key: str | None = None,
    ) -> ParentRun:
        """Atomically mark the Parent and cancel every not-yet-running Child."""
        emitted: list[RunEvent] = []
        with self._write_session() as db:
            row = self._require_parent_row(db, parent_run_id)
            current = ParentRun.model_validate_json(row.payload_json)
            if current.version != expected_version and expected_version is not None:
                if idempotency_key is not None:
                    existing = db.scalar(select(RunEventRow).where(
                        RunEventRow.parent_run_id == parent_run_id,
                        RunEventRow.event_type == "parent.cancellation_requested",
                    ).order_by(RunEventRow.event_seq.desc()))
                    if existing is not None:
                        persisted = RunEvent.model_validate_json(existing.payload_json)
                        if persisted.payload.get("idempotency_key") == idempotency_key:
                            return current
                raise RevisionConflictError(
                    f"Parent {parent_run_id} expected version {expected_version}, found {current.version}"
                )
            if current.status in {"cancelled", "succeeded", "partial", "failed", "timed_out", "budget_exhausted"}:
                return current
            if current.status == "cancelling":
                return current
            updated = transition_run(current, "cancelling", expected_version=current.version, occurred_at=requested_at).model_copy(
                update={
                    "cancellation_version": current.cancellation_version + 1,
                    "cancel_requested_at": requested_at,
                    "phase": "cancelling",
                }
            )
            updated = ParentRun.model_validate(updated.model_dump(mode="python"))
            row.status, row.version, row.updated_at = updated.status, updated.version, updated.updated_at
            row.payload_json = updated.model_dump_json()
            self._append_status_event(db, current=current, updated=updated, occurred_at=requested_at)
            event = RunEvent(
                id=f"parent-cancel:{parent_run_id}:{updated.cancellation_version}",
                parent_run_id=parent_run_id, event_type="parent.cancellation_requested",
                status="completed", occurred_at=requested_at,
                payload={
                    "reason": reason, "cancellation_version": updated.cancellation_version,
                    "idempotency_key": idempotency_key,
                },
            )
            event = self._assign_event_sequence(db, event)
            db.add(self._event_row(event))
            emitted.append(event)
            child_rows = db.scalars(
                select(ChildRunRow).where(
                    ChildRunRow.parent_run_id == parent_run_id,
                    ChildRunRow.status == "queued",
                )
            ).all()
            for child_row in child_rows:
                child = ChildRun.model_validate_json(child_row.payload_json)
                cancelling = transition_run(child, "cancelling", expected_version=child.version, occurred_at=requested_at)
                cancelled = transition_run(cancelling, "cancelled", expected_version=cancelling.version, occurred_at=requested_at)
                child_row.status, child_row.version, child_row.updated_at = cancelled.status, cancelled.version, cancelled.updated_at
                child_row.payload_json = cancelled.model_dump_json()
                self._append_status_event(db, current=child, updated=cancelling, occurred_at=requested_at)
                self._append_status_event(db, current=cancelling, updated=cancelled, occurred_at=requested_at)
            terminal = {
                "succeeded", "partial", "failed", "timed_out",
                "budget_exhausted", "cancelled",
            }
            all_children = db.scalars(
                select(ChildRunRow).where(ChildRunRow.parent_run_id == parent_run_id)
            ).all()
            if all_children and all(item.status in terminal for item in all_children):
                cancelled_parent = transition_run(
                    updated,
                    "cancelled",
                    expected_version=updated.version,
                    occurred_at=requested_at,
                ).model_copy(update={"phase": "children_terminal"})
                cancelled_parent = ParentRun.model_validate(
                    cancelled_parent.model_dump(mode="python")
                )
                row.status = cancelled_parent.status
                row.version = cancelled_parent.version
                row.updated_at = cancelled_parent.updated_at
                row.payload_json = cancelled_parent.model_dump_json()
                self._append_status_event(
                    db,
                    current=updated,
                    updated=cancelled_parent,
                    occurred_at=requested_at,
                )
                updated = cancelled_parent
        return updated

    def mark_parent_backfill_completed(
        self, parent_run_id: str, *, result_version: int, message_id: str, occurred_at: datetime,
    ) -> ParentRun:
        """Record the durable Chat side effect after its deterministic message write."""
        with self._write_session() as db:
            row = self._require_parent_row(db, parent_run_id)
            current = ParentRun.model_validate_json(row.payload_json)
            if current.result_version != result_version:
                raise RevisionConflictError("backfill result version is stale")
            if current.backfill_status == "completed":
                if current.backfill_message_id == message_id:
                    return current
                raise IdempotencyPayloadConflictError("backfill already bound to another message")
            updated = ParentRun.model_validate({
                **current.model_dump(mode="python"), "backfill_status": "completed",
                "backfill_message_id": message_id, "version": current.version + 1,
                "updated_at": occurred_at,
            })
            row.version, row.updated_at, row.payload_json = (
                updated.version, updated.updated_at, updated.model_dump_json()
            )
            backfill_event = RunEvent(
                id=f"chat-backfill:{parent_run_id}:{result_version}", parent_run_id=parent_run_id,
                event_type="chat.backfill_completed", status="completed", occurred_at=occurred_at,
                payload={"result_version": result_version, "message_id": message_id},
            )
            existing = db.get(RunEventRow, backfill_event.id)
            if existing is None:
                db.add(self._event_row(self._assign_event_sequence(db, backfill_event)))
            return updated

    def parent_cancellation_version(self, parent_run_id: str) -> tuple[int, bool]:
        parent = self.get_parent(parent_run_id)
        if parent is None:
            raise RecordNotFoundError(f"Parent run not found: {parent_run_id}")
        return parent.cancellation_version, parent.status in {"cancelling", "cancelled"}

    def save_child_checkpoint(self, child_run_id: str, checkpoint: dict[str, Any]) -> str:
        controlled = TypeAdapter(BoundedJsonObject).validate_python(checkpoint)
        digest = canonical_json_sha256(controlled)
        ref = f"checkpoint:job-web:{child_run_id}:{digest}"
        created_at = datetime.fromisoformat(str(controlled["created_at"]))
        with self._write_session() as db:
            child_row = db.get(ChildRunRow, child_run_id)
            if child_row is None:
                raise RecordNotFoundError(f"Child run not found: {child_run_id}")
            child = ChildRun.model_validate_json(child_row.payload_json)
            task_row = db.get(ChildTaskRow, child.child_task_id)
            parent_row = db.get(ParentRunRow, child.parent_run_id)
            if task_row is None or parent_row is None:
                raise ValueError("handoff_checkpoint_authority_mismatch")
            task = ChildTask.model_validate_json(task_row.payload_json)
            parent = ParentRun.model_validate_json(parent_row.payload_json)
            expected = {
                "parent_run_id": parent.id, "child_task_id": task.id,
                "child_run_id": child.id, "principal": parent.principal,
            }
            if child.status != "running" or any(controlled.get(key) != value for key, value in expected.items()):
                raise ValueError("handoff_checkpoint_authority_mismatch")
            existing = db.get(ChildCheckpointRow, ref)
            if existing is None:
                db.add(ChildCheckpointRow(
                    ref=ref, child_run_id=child_run_id, created_at=created_at,
                    payload_json=TypeAdapter(BoundedJsonObject).dump_json(controlled).decode("utf-8"),
                ))
        return ref

    def load_child_checkpoint(
        self, ref: str, *, parent_run_id: str, child_task_id: str,
        child_run_id: str, principal: str, now: datetime, timeout_seconds: int,
    ) -> dict[str, Any]:
        with Session(self.engine) as db:
            row = db.get(ChildCheckpointRow, ref)
            if row is None:
                raise RecordNotFoundError(f"Child checkpoint not found: {ref}")
            checkpoint = dict(TypeAdapter(BoundedJsonObject).validate_json(row.payload_json))
            child_row = db.get(ChildRunRow, child_run_id)
            task_row = db.get(ChildTaskRow, child_task_id)
            parent_row = db.get(ParentRunRow, parent_run_id)
            if child_row is None or task_row is None or parent_row is None:
                raise ValueError("handoff_checkpoint_authority_mismatch")
            child = ChildRun.model_validate_json(child_row.payload_json)
            task = ChildTask.model_validate_json(task_row.payload_json)
            parent = ParentRun.model_validate_json(parent_row.payload_json)
        authority = {
            "parent_run_id": parent_run_id, "child_task_id": child_task_id,
            "child_run_id": child_run_id, "principal": principal,
        }
        persisted_valid = (
            child.status == "waiting_for_user"
            and child.run_context_checkpoint_ref == ref
            and child.child_task_id == task.id
            and child.parent_run_id == parent.id
            and task.parent_run_id == parent.id
            and parent.principal == principal
        )
        if not persisted_valid or checkpoint.get("version") != "job-web-handoff-v1" or any(
            checkpoint.get(key) != value for key, value in authority.items()
        ):
            raise ValueError("handoff_checkpoint_authority_mismatch")
        created_at = datetime.fromisoformat(str(checkpoint["created_at"]))
        if (now - created_at).total_seconds() > timeout_seconds:
            raise TimeoutError("job_web_handoff_timeout")
        return checkpoint

    def resume_child_from_checkpoint(
        self, ref: str, *, parent_run_id: str, child_task_id: str,
        child_run_id: str, principal: str, now: datetime, timeout_seconds: int,
    ) -> ChildRun:
        checkpoint = self.load_child_checkpoint(
            ref, parent_run_id=parent_run_id, child_task_id=child_task_id,
            child_run_id=child_run_id, principal=principal, now=now,
            timeout_seconds=timeout_seconds,
        )
        with self._write_session() as db:
            row = db.get(ChildRunRow, child_run_id)
            current = ChildRun.model_validate_json(row.payload_json)
            if current.status != "waiting_for_user" or current.run_context_checkpoint_ref != ref:
                raise RevisionConflictError(f"Child resume conflict: {child_run_id}")
            updated = transition_run(current, "queued", expected_version=current.version, occurred_at=now).model_copy(
                update={"phase": str(checkpoint["next_phase"]), "run_context_checkpoint_ref": ref, "available_at": now}
            )
            result = db.execute(update(ChildRunRow).where(
                ChildRunRow.id == child_run_id, ChildRunRow.status == "waiting_for_user",
                ChildRunRow.version == current.version,
            ).values(status=updated.status, version=updated.version, updated_at=updated.updated_at, payload_json=updated.model_dump_json()))
            if result.rowcount != 1:
                raise RevisionConflictError(f"Child resume conflict: {child_run_id}")
            return updated

    def load_child_checkpoint_for_worker(
        self, ref: str, *, parent_run_id: str, child_task_id: str,
        child_run_id: str, principal: str,
    ) -> dict[str, Any]:
        with Session(self.engine) as db:
            row = db.get(ChildCheckpointRow, ref)
            child_row = db.get(ChildRunRow, child_run_id)
            task_row = db.get(ChildTaskRow, child_task_id)
            parent_row = db.get(ParentRunRow, parent_run_id)
            if row is None or child_row is None or task_row is None or parent_row is None:
                raise ValueError("handoff_checkpoint_authority_mismatch")
            checkpoint = dict(TypeAdapter(BoundedJsonObject).validate_json(row.payload_json))
            child = ChildRun.model_validate_json(child_row.payload_json)
            task = ChildTask.model_validate_json(task_row.payload_json)
            parent = ParentRun.model_validate_json(parent_row.payload_json)
        if (
            child.status != "running" or child.run_context_checkpoint_ref != ref
            or child.child_task_id != task.id or child.parent_run_id != parent.id
            or task.parent_run_id != parent.id or parent.principal != principal
            or checkpoint.get("parent_run_id") != parent_run_id
            or checkpoint.get("child_task_id") != child_task_id
            or checkpoint.get("child_run_id") != child_run_id
            or checkpoint.get("principal") != principal
            or not isinstance(checkpoint.get("run_context"), dict)
        ):
            raise ValueError("handoff_checkpoint_authority_mismatch")
        return checkpoint

    def release_child_lease(
        self,
        child_run_id: str,
        *,
        target_status: Literal["queued", "waiting_for_user", "cancelled", "failed", "timed_out"],
        worker_id: str,
        lease_token: str,
        expected_version: int,
        occurred_at: datetime,
        error_code: str | None = None,
        available_at: datetime | None = None,
        increment_attempt: bool = False,
        event_type: str | None = None,
        checkpoint_ref: str | None = None,
    ) -> ChildRun:
        """Release a live lease with CAS; used for retry, suspension and cancellation."""
        with self._write_session() as db:
            row = db.get(ChildRunRow, child_run_id)
            if row is None:
                raise RecordNotFoundError(f"Child run not found: {child_run_id}")
            current = ChildRun.model_validate_json(row.payload_json)
            if (
                current.status != "running"
                or current.version != expected_version
                or current.lease_owner != worker_id
                or current.lease_token != lease_token
            ):
                raise RevisionConflictError(f"Child lease conflict: {child_run_id}")
            if target_status == "queued":
                data = {
                    **current.model_dump(mode="python"),
                    "status": "queued", "phase": "queued",
                    "version": current.version + 1,
                    "attempt": current.attempt + (1 if increment_attempt else 0),
                    "available_at": available_at or occurred_at,
                    "lease_owner": None, "lease_token": None,
                    "lease_expires_at": None, "heartbeat_at": None,
                    "error_code": error_code, "retryable": True,
                    "updated_at": occurred_at,
                }
            elif target_status == "cancelled":
                cancelling = transition_run(current, "cancelling", expected_version=current.version, occurred_at=occurred_at)
                terminal = transition_run(cancelling, "cancelled", expected_version=cancelling.version, occurred_at=occurred_at)
                data = {**terminal.model_dump(mode="python"), "phase": "cancelled", "error_code": error_code or "run_cancelled"}
            else:
                terminal = transition_run(current, target_status, expected_version=current.version, occurred_at=occurred_at)
                data = {
                    **terminal.model_dump(mode="python"), "phase": target_status,
                    "error_code": error_code,
                    "run_context_checkpoint_ref": checkpoint_ref or terminal.run_context_checkpoint_ref,
                }
            data.update({"lease_owner": None, "lease_token": None, "lease_expires_at": None, "heartbeat_at": None})
            updated = ChildRun.model_validate(data)
            result = db.execute(
                update(ChildRunRow)
                .where(
                    ChildRunRow.id == child_run_id,
                    ChildRunRow.status == "running",
                    ChildRunRow.version == expected_version,
                    ChildRunRow.lease_owner == worker_id,
                    ChildRunRow.lease_token == lease_token,
                )
                .values(
                    status=updated.status, version=updated.version,
                    lease_owner=None, lease_token=None, lease_expires_at=None,
                    heartbeat_at=None, updated_at=updated.updated_at,
                    payload_json=updated.model_dump_json(),
                )
            )
            if result.rowcount != 1:
                raise RevisionConflictError(f"Child lease conflict: {child_run_id}")
            self._append_status_event(db, current=current, updated=updated, occurred_at=occurred_at)
            if event_type is not None:
                event = RunEvent(
                    id=f"{event_type}:{child_run_id}:{updated.version}",
                    parent_run_id=current.parent_run_id, child_run_id=child_run_id,
                    event_type=event_type, status="completed", occurred_at=occurred_at,
                    payload={"attempt": updated.attempt, "error_code": error_code},
                )
                db.add(self._event_row(self._assign_event_sequence(db, event)))
            return updated

    def list_expired_child_leases(self, *, now: datetime, limit: int = 100) -> tuple[ChildRun, ...]:
        with Session(self.engine) as db:
            rows = db.scalars(
                select(ChildRunRow)
                .where(ChildRunRow.status == "running", ChildRunRow.lease_expires_at < now)
                .order_by(ChildRunRow.lease_expires_at, ChildRunRow.id)
                .limit(limit)
            ).all()
            return tuple(ChildRun.model_validate_json(row.payload_json) for row in rows)

    def expire_queued_child_runs(self, *, now: datetime, limit: int = 100) -> tuple[ChildRun, ...]:
        with self._write_session() as db:
            rows = db.scalars(
                select(ChildRunRow)
                .where(ChildRunRow.status == "queued")
                .order_by(ChildRunRow.created_at, ChildRunRow.id)
                .limit(limit)
            ).all()
            expired: list[ChildRun] = []
            for row in rows:
                current = ChildRun.model_validate_json(row.payload_json)
                if now < current.deadline_at:
                    continue
                updated = transition_run(current, "running", expected_version=current.version, occurred_at=now).model_copy(
                    update={"started_at": now}
                )
                updated = transition_run(updated, "timed_out", expected_version=updated.version, occurred_at=now).model_copy(
                    update={"error_code": "run_deadline_exceeded", "phase": "timed_out"}
                )
                updated = ChildRun.model_validate(updated.model_dump(mode="python"))
                row.status, row.version, row.updated_at = updated.status, updated.version, updated.updated_at
                row.payload_json = updated.model_dump_json()
                self._append_status_event(db, current=current, updated=updated, occurred_at=now)
                event = RunEvent(
                    id=f"child.deadline_exceeded:{current.id}:{updated.version}",
                    parent_run_id=current.parent_run_id,
                    child_run_id=current.id,
                    event_type="child.deadline_exceeded",
                    status="completed",
                    occurred_at=now,
                    payload={"error_code": "run_deadline_exceeded", "deadline_at": current.deadline_at.isoformat()},
                )
                db.add(self._event_row(self._assign_event_sequence(db, event)))
                expired.append(updated)
            return tuple(expired)

    def wake_parent_if_children_terminal(self, parent_run_id: str, *, occurred_at: datetime) -> ParentRun | None:
        with self._write_session() as db:
            row = self._require_parent_row(db, parent_run_id)
            parent = ParentRun.model_validate_json(row.payload_json)
            if parent.status not in {"waiting_children", "cancelling"}:
                return None
            child_rows = db.scalars(select(ChildRunRow).where(ChildRunRow.parent_run_id == parent_run_id)).all()
            terminal = {"succeeded", "partial", "failed", "timed_out", "budget_exhausted", "cancelled"}
            if not child_rows or any(item.status not in terminal for item in child_rows):
                return None
            target = "cancelled" if parent.status == "cancelling" else "queued"
            updated = transition_run(parent, target, expected_version=parent.version, occurred_at=occurred_at).model_copy(update={"phase": "children_terminal"})
            updated = ParentRun.model_validate(updated.model_dump(mode="python"))
            row.status, row.version, row.updated_at = updated.status, updated.version, updated.updated_at
            row.payload_json = updated.model_dump_json()
            self._append_status_event(db, current=parent, updated=updated, occurred_at=occurred_at)
            return updated

    def create_child_task_and_run(
        self,
        *,
        contract: TaskContract,
        child_run: ChildRun,
        specialist_snapshot_id: str,
        output_schema_version: str,
        expected_parent_version: int,
        created_at: datetime,
        creation_event: RunEvent | None = None,
        outbox_message: OutboxMessage | None = None,
        queue_hard_capacity: int | None = None,
    ) -> ChildCreationResult:
        if child_run.parent_run_id != contract.parent_run_id:
            raise ValueError("child run parent does not match task contract")
        if child_run.child_task_id != contract.task_id:
            raise ValueError("child run task does not match task contract")
        if child_run.deadline_at != contract.requested_deadline:
            raise ValueError("child run deadline does not match task contract")
        emitted: list[RunEvent] = []
        try:
            with self._write_session() as db:
                existing = db.scalar(
                    select(ChildTaskRow).where(
                        ChildTaskRow.parent_run_id == contract.parent_run_id,
                        ChildTaskRow.idempotency_key == contract.idempotency_key
                    )
                )
                if existing is not None:
                    if existing.contract_hash != contract.canonical_hash:
                        raise IdempotencyPayloadConflictError(
                            "task idempotency key is bound to a different contract"
                        )
                    task = ChildTask.model_validate_json(existing.payload_json)
                    run_row = db.scalar(
                        select(ChildRunRow)
                        .where(ChildRunRow.child_task_id == task.id)
                        .order_by(ChildRunRow.attempt.desc())
                    )
                    if run_row is None:
                        raise RunStoreError("idempotent task has no child run")
                    run = ChildRun.model_validate_json(run_row.payload_json)
                    parent_row = self._require_parent_row(db, task.parent_run_id)
                    return ChildCreationResult(
                        parent=ParentRun.model_validate_json(parent_row.payload_json),
                        task=task,
                        run=run,
                        allocations=self._allocations_for_run(db, run.id),
                    )

                if queue_hard_capacity is not None:
                    if queue_hard_capacity < 1:
                        raise ValueError("queue_hard_capacity must be positive")
                    queued_count = len(
                        db.scalars(
                            select(ChildRunRow.id).where(ChildRunRow.status == "queued")
                        ).all()
                    )
                    if queued_count >= queue_hard_capacity:
                        raise RunStoreError(
                            "delegation run queue reached hard capacity",
                            code="run_queue_overloaded",
                        )

                parent_row = self._require_parent_row(db, contract.parent_run_id)
                if parent_row.version != expected_parent_version:
                    raise RevisionConflictError(
                        f"Parent {parent_row.id} expected version "
                        f"{expected_parent_version}, found {parent_row.version}"
                    )
                parent = ParentRun.model_validate_json(parent_row.payload_json)
                if parent.status in {"cancelling", "cancelled"} or parent.cancel_requested_at is not None:
                    raise RunStoreError(
                        f"Parent run is cancelling: {parent.id}",
                        code="parent_cancelling",
                    )
                if contract.requested_deadline > parent.deadline_at:
                    raise ValueError("child deadline cannot exceed parent deadline")
                reservation = reserve_budget(
                    total=parent.budget_total,
                    reserved=parent.budget_reserved,
                    consumed=parent.budget_consumed,
                    requested=contract.requested_budget,
                )
                updated_parent = ParentRun.model_validate(
                    {
                        **parent.model_dump(mode="python"),
                        "budget_reserved": reservation.updated_reserved,
                        "version": parent.version + 1,
                        "updated_at": created_at,
                    }
                )
                result = db.execute(
                    update(ParentRunRow)
                    .where(
                        ParentRunRow.id == parent.id,
                        ParentRunRow.version == expected_parent_version,
                    )
                    .values(
                        version=updated_parent.version,
                        status=updated_parent.status,
                        updated_at=updated_parent.updated_at,
                        payload_json=updated_parent.model_dump_json(),
                    )
                )
                if result.rowcount != 1:
                    raise RevisionConflictError(
                        f"Parent revision conflict: {parent.id}"
                    )
                task = ChildTask.from_contract(
                    contract,
                    specialist_snapshot_id=specialist_snapshot_id,
                    output_schema_version=output_schema_version,
                    created_at=created_at,
                )
                db.add(self._task_row(task))
                db.flush()
                db.add(self._child_run_row(child_run))
                db.flush()
                for allocation in reservation.allocations:
                    db.add(
                        BudgetAllocationRow(
                            id=f"{child_run.id}:{allocation.dimension}",
                            parent_run_id=parent.id,
                            child_task_id=task.id,
                            child_run_id=child_run.id,
                            dimension=allocation.dimension,
                            version=allocation.version,
                            payload_json=allocation.model_dump_json(),
                        )
                    )
                db.flush()
                budget_event = RunEvent(
                    id=f"budget-reserved:{child_run.id}:{updated_parent.version}",
                    parent_run_id=parent.id, child_run_id=child_run.id,
                    event_type="budget.reserved", status="completed", occurred_at=created_at,
                    payload={"task_id": task.id, "requested_budget": contract.requested_budget.model_dump(mode="json"), "reserved_budget": updated_parent.budget_reserved.model_dump(mode="json")},
                )
                budget_event = self._assign_event_sequence(db, budget_event)
                db.add(self._event_row(budget_event))
                emitted.append(budget_event)
                if creation_event is not None:
                    if (
                        creation_event.parent_run_id != parent.id
                        or creation_event.child_run_id != child_run.id
                    ):
                        raise ValueError("creation event does not match child run")
                    creation_event = self._assign_event_sequence(db, creation_event)
                    db.add(self._event_row(creation_event))
                    emitted.append(creation_event)
                if outbox_message is not None:
                    if outbox_message.aggregate_id != child_run.id:
                        raise ValueError("outbox aggregate does not match child run")
                    existing_outbox = db.scalar(
                        select(OutboxRow).where(
                            OutboxRow.idempotency_key
                            == outbox_message.idempotency_key
                        )
                    )
                    if existing_outbox is not None:
                        raise IdempotencyPayloadConflictError(
                            "Outbox idempotency key is already bound"
                        )
                    db.add(self._outbox_row(outbox_message))
                db.flush()
                result_value = ChildCreationResult(
                    parent=updated_parent,
                    task=task,
                    run=child_run,
                    allocations=reservation.allocations,
                )
        except IntegrityError as exc:
            raise RunStoreError(
                "child task/run creation violated a persistence constraint",
                code="run_store_integrity_error",
            ) from exc
        return result_value

    def _accept_child_result(
        self,
        child_run_id: str,
        *,
        result_envelope_ref: str,
        result_hash: str,
        expected_task_version: int,
        accepted_at: datetime,
    ) -> ChildTask:
        with self._write_session() as db:
            child_row = db.get(ChildRunRow, child_run_id)
            if child_row is None:
                raise RecordNotFoundError(f"Child run not found: {child_run_id}")
            task_row = db.get(ChildTaskRow, child_row.child_task_id)
            if task_row is None:
                raise RecordNotFoundError(
                    f"Child task not found: {child_row.child_task_id}"
                )
            current = ChildTask.model_validate_json(task_row.payload_json)
            child = ChildRun.model_validate_json(child_row.payload_json)
            if current.version != expected_task_version or current.accepted_child_run_id:
                raise RevisionConflictError(
                    f"Child task result acceptance conflict: {current.id}"
                )
            if (
                child.status not in {"succeeded", "partial"}
                or child.result_envelope_ref != result_envelope_ref
                or child.result_hash != result_hash
            ):
                raise RunStoreError(
                    f"Child result is not eligible for acceptance: {child_run_id}",
                    code="child_result_not_acceptable",
                )
            updated = ChildTask.model_validate(
                {
                    **current.model_dump(mode="python"),
                    "version": current.version + 1,
                    "accepted_child_run_id": child_run_id,
                    "accepted_result_envelope_ref": result_envelope_ref,
                    "accepted_result_hash": result_hash,
                    "accepted_at": accepted_at,
                    "updated_at": accepted_at,
                }
            )
            result = db.execute(
                update(ChildTaskRow)
                .where(
                    ChildTaskRow.id == current.id,
                    ChildTaskRow.version == expected_task_version,
                    ChildTaskRow.accepted_child_run_id.is_(None),
                )
                .values(
                    version=updated.version,
                    accepted_child_run_id=child_run_id,
                    payload_json=updated.model_dump_json(),
                )
            )
            if result.rowcount != 1:
                raise RevisionConflictError(
                    f"Child task result acceptance conflict: {current.id}"
                )
            return updated

    def record_result_repair_attempt(self, attempt: ResultRepairAttempt) -> ResultRepairAttempt:
        with self._write_session() as db:
            child = db.get(ChildRunRow, attempt.child_run_id)
            if child is None:
                raise RecordNotFoundError(f"Child run not found: {attempt.child_run_id}")
            parent_row = self._require_parent_row(db, child.parent_run_id)
            parent = ParentRun.model_validate_json(parent_row.payload_json)
            existing = db.scalar(select(RunEventRow).where(RunEventRow.id == f"result-repair:{attempt.child_run_id}"))
            if existing is not None:
                persisted = RunEvent.model_validate_json(existing.payload_json)
                if persisted.payload.get("envelope_hash") == attempt.envelope_hash:
                    return attempt
                raise MergeConflictError("merge_conflict: repair already attempted")
            if parent.version != attempt.expected_parent_version:
                raise MergeConflictError("merge_conflict: repair parent version changed")
            settlement = settle_budget(parent_reserved=parent.budget_reserved, parent_consumed=parent.budget_consumed, allocations=self._allocations_for_run(db, child.id), usage=attempt.usage)
            updated_parent = ParentRun.model_validate({**parent.model_dump(mode="python"), "budget_reserved": settlement.updated_reserved, "budget_consumed": settlement.updated_consumed, "version": parent.version + 1, "updated_at": attempt.occurred_at})
            result = db.execute(update(ParentRunRow).where(ParentRunRow.id == parent.id, ParentRunRow.version == parent.version).values(version=updated_parent.version, updated_at=updated_parent.updated_at, payload_json=updated_parent.model_dump_json()))
            if result.rowcount != 1:
                raise MergeConflictError("merge_conflict: repair parent CAS failed")
            for allocation in settlement.allocations:
                row = db.get(BudgetAllocationRow, f"{child.id}:{allocation.dimension}")
                if row is None:
                    raise RecordNotFoundError(f"Budget allocation missing: {child.id}:{allocation.dimension}")
                row.version, row.payload_json = allocation.version, allocation.model_dump_json()
            self._append_budget_event(
                db, event_id=f"budget-repair-settled:{child.id}:{updated_parent.version}",
                parent=updated_parent, child_run_id=child.id,
                occurred_at=attempt.occurred_at, event_type="budget.consumed_released",
                usage=attempt.usage,
            )
            event = RunEvent(id=f"result-repair:{attempt.child_run_id}", parent_run_id=child.parent_run_id, child_run_id=attempt.child_run_id, event_type="result.repair_requested", status="completed", occurred_at=attempt.occurred_at, payload={"attempt": attempt.attempt, "envelope_hash": attempt.envelope_hash, "error_code": attempt.error_code})
            db.add(self._event_row(self._assign_event_sequence(db, event)))
            return attempt

    def begin_result_repair_attempt(self, *, child_run_id: str, envelope_hash: str, expected_parent_version: int) -> None:
        """Reserve the minimum repair call before its no-tool Provider invocation."""
        with self._write_session() as db:
            child = db.get(ChildRunRow, child_run_id)
            if child is None:
                raise RecordNotFoundError(f"Child run not found: {child_run_id}")
            parent_row = self._require_parent_row(db, child.parent_run_id)
            parent = ParentRun.model_validate_json(parent_row.payload_json)
            if parent.status in {"cancelling", "cancelled", "succeeded", "partial", "failed", "timed_out", "budget_exhausted"}:
                raise MergeConflictError("merge_conflict: repair parent is not active")
            if child.status not in {"running", "waiting_for_user"}:
                raise MergeConflictError("merge_conflict: repair child is not active")
            if parent.version != expected_parent_version:
                raise MergeConflictError("merge_conflict: repair parent version changed")
            if db.get(ResultRepairRow, child_run_id) is not None:
                raise MergeConflictError("merge_conflict: repair already attempted")
            allocations = self._allocations_for_run(db, child.id)
            repair_minimum = {
                "steps": 0,
                "tokens": 1,
                "cost_microunits": 1,
                "wall_clock_ms": 1,
                "model_calls": 1,
                "tool_calls": 0,
            }
            if any(
                item.reserved - item.consumed - item.released
                < repair_minimum[item.dimension]
                for item in allocations
            ):
                raise RunStoreError("repair budget insufficient", code="repair_budget_insufficient")
            attempt = ResultRepairAttempt(child_run_id=child.id, envelope_hash=envelope_hash, attempt=1, error_code="result_schema_invalid", expected_parent_version=parent.version, occurred_at=datetime.now(UTC))
            db.add(ResultRepairRow(child_run_id=child.id, envelope_hash=envelope_hash, status="pending", parent_version=parent.version, payload_json=attempt.model_dump_json()))
            event = RunEvent(id=f"result-repair:{child.id}", parent_run_id=parent.id, child_run_id=child.id, event_type="result.repair_started", status="completed", occurred_at=attempt.occurred_at, payload={"attempt": 1, "envelope_hash": envelope_hash, "status": "pending"})
            db.add(self._event_row(self._assign_event_sequence(db, event)))

    def complete_result_repair_attempt(self, completion: ResultRepairCompletion) -> None:
        with self._write_session() as db:
            repair = db.get(ResultRepairRow, completion.child_run_id)
            child = db.get(ChildRunRow, completion.child_run_id)
            if repair is None or child is None or repair.envelope_hash != completion.envelope_hash:
                raise MergeConflictError("merge_conflict: repair attempt not found")
            parent_row = self._require_parent_row(db, child.parent_run_id)
            parent = ParentRun.model_validate_json(parent_row.payload_json)
            if repair.status != "pending":
                return
            if completion.usage is not None and completion.usage.cost_status != "unknown":
                settlement = settle_budget(parent_reserved=parent.budget_reserved, parent_consumed=parent.budget_consumed, allocations=self._allocations_for_run(db, child.id), usage=completion.usage, release_unused=False)
                updated = ParentRun.model_validate({**parent.model_dump(mode="python"), "budget_reserved": settlement.updated_reserved, "budget_consumed": settlement.updated_consumed, "version": parent.version + 1, "updated_at": completion.occurred_at})
                db.execute(update(ParentRunRow).where(ParentRunRow.id == parent.id, ParentRunRow.version == parent.version).values(version=updated.version, updated_at=updated.updated_at, payload_json=updated.model_dump_json()))
                for allocation in settlement.allocations:
                    row = db.get(BudgetAllocationRow, f"{child.id}:{allocation.dimension}")
                    row.version, row.payload_json = allocation.version, allocation.model_dump_json()
                repair.parent_version = updated.version
                self._append_budget_event(
                    db, event_id=f"budget-repair-completed:{child.id}:{updated.version}",
                    parent=updated, child_run_id=child.id,
                    occurred_at=completion.occurred_at, event_type="budget.consumed",
                    usage=completion.usage,
                )
            repair.status = completion.status
            repair.payload_json = completion.model_dump_json()
            event = RunEvent(id=f"result-repair-completed:{child.id}", parent_run_id=child.parent_run_id, child_run_id=child.id, event_type="result.repair_completed", status="completed", occurred_at=completion.occurred_at, payload={"status": completion.status, "envelope_hash": completion.envelope_hash})
            db.add(self._event_row(self._assign_event_sequence(db, event)))

    def recover_pending_result_repairs(self, *, now: datetime, stale_after: timedelta) -> tuple[str, ...]:
        """Mark abandoned provider calls; recovery never invokes a provider again."""
        if stale_after <= timedelta(0):
            raise ValueError("repair stale_after must be positive")
        recovered: list[str] = []
        with self._write_session() as db:
            rows = db.scalars(select(ResultRepairRow).where(ResultRepairRow.status == "pending")).all()
            for row in rows:
                attempt = ResultRepairAttempt.model_validate_json(row.payload_json)
                if now - attempt.occurred_at < stale_after:
                    continue
                child = db.get(ChildRunRow, row.child_run_id)
                if child is None:
                    continue
                row.status = "abandoned_usage_unknown"
                row.payload_json = ResultRepairCompletion(child_run_id=child.id, envelope_hash=row.envelope_hash, status="abandoned_usage_unknown", expected_parent_version=row.parent_version, occurred_at=now).model_dump_json()
                event = RunEvent(id=f"result-repair-abandoned:{child.id}", parent_run_id=child.parent_run_id, child_run_id=child.id, event_type="result.repair_abandoned", status="completed", occurred_at=now, payload={"envelope_hash": row.envelope_hash, "status": "abandoned_usage_unknown"})
                db.add(self._event_row(self._assign_event_sequence(db, event)))
                recovered.append(child.id)
        return tuple(recovered)

    def accept_validated_result(self, command: ValidatedResultAcceptance) -> ChildTask:
        """The sole transactional acceptance path for validated Child Envelopes."""
        with self._write_session() as db:
            child_row = db.get(ChildRunRow, command.child_run_id)
            if child_row is None:
                raise RecordNotFoundError(f"Child run not found: {command.child_run_id}")
            task_row = db.get(ChildTaskRow, child_row.child_task_id)
            parent_row = self._require_parent_row(db, child_row.parent_run_id)
            parent = ParentRun.model_validate_json(parent_row.payload_json)
            task = ChildTask.model_validate_json(task_row.payload_json)
            child = ChildRun.model_validate_json(child_row.payload_json)
            if parent.status in {"cancelling", "cancelled", "succeeded", "partial", "failed", "timed_out", "budget_exhausted"}:
                raise MergeConflictError("merge_conflict: parent is not mergeable")
            if parent.version != command.expected_parent_version or task.version != command.expected_task_version:
                raise MergeConflictError("merge_conflict: expected version changed")
            if task.accepted_child_run_id is not None:
                if task.accepted_child_run_id == child.id and task.accepted_result_hash == command.envelope_hash and task.accepted_result_envelope_ref == command.envelope_ref:
                    return task
                raise MergeConflictError("merge_conflict: task already accepted another result")
            if child.status not in {"succeeded", "partial"} or child.result_envelope_ref != command.envelope_ref or child.result_hash != command.envelope_hash:
                raise RunStoreError("validated result no longer matches terminal Child", code="child_result_not_acceptable")
            allocations = self._allocations_for_run(db, child.id)
            settlement = settle_budget(parent_reserved=parent.budget_reserved, parent_consumed=parent.budget_consumed, allocations=allocations, usage=command.usage)
            accepted = ChildTask.model_validate({**task.model_dump(mode="python"), "version": task.version + 1, "accepted_child_run_id": child.id, "accepted_result_envelope_ref": command.envelope_ref, "accepted_result_hash": command.envelope_hash, "accepted_at": command.accepted_at, "updated_at": command.accepted_at})
            updated_parent_data: dict[str, Any] = {**parent.model_dump(mode="python"), "budget_reserved": settlement.updated_reserved, "budget_consumed": settlement.updated_consumed, "version": parent.version + 1, "updated_at": command.accepted_at}
            if command.merge_report is not None:
                if command.merge_report.parent_run_id != parent.id or command.merge_report.result_version != parent.result_version + 1:
                    raise MergeConflictError("merge_conflict: report version invalid")
                updated_parent_data.update({"result_version": command.merge_report.result_version, "merge_report_id": command.merge_report.id})
                db.add(MergeReportRow(id=command.merge_report.id, parent_run_id=parent.id, result_version=command.merge_report.result_version, created_at=command.merge_report.created_at, payload_json=command.merge_report.model_dump_json()))
            for candidate in command.candidates:
                if candidate.parent_run_id != parent.id or candidate.expected_parent_version != command.expected_parent_version:
                    raise MergeConflictError("merge_conflict: candidate parent/version mismatch")
                existing = db.scalar(select(CandidateMergeRow).where(CandidateMergeRow.parent_run_id == parent.id, CandidateMergeRow.idempotency_key == candidate.idempotency_key))
                if existing is not None:
                    if existing.payload_hash != candidate.payload_hash or existing.candidate_key != candidate.candidate_key:
                        raise MergeConflictError("merge_conflict: candidate idempotency conflict")
                    continue
                same_key = db.scalar(select(CandidateMergeRow).where(CandidateMergeRow.parent_run_id == parent.id, CandidateMergeRow.candidate_key == candidate.candidate_key))
                if same_key is not None:
                    raise MergeConflictError("merge_conflict: candidate uniqueness conflict")
                db.add(CandidateMergeRow(id=candidate.id, parent_run_id=parent.id, candidate_key=candidate.candidate_key, idempotency_key=candidate.idempotency_key, payload_hash=candidate.payload_hash, parent_version=parent.version + 1, created_at=candidate.created_at, payload_json=candidate.model_dump_json()))
            updated_parent = ParentRun.model_validate(updated_parent_data)
            result = db.execute(update(ParentRunRow).where(ParentRunRow.id == parent.id, ParentRunRow.version == parent.version).values(version=updated_parent.version, updated_at=updated_parent.updated_at, payload_json=updated_parent.model_dump_json()))
            if result.rowcount != 1:
                raise MergeConflictError("merge_conflict: parent CAS failed")
            task_row.version, task_row.accepted_child_run_id, task_row.payload_json = accepted.version, child.id, accepted.model_dump_json()
            for allocation in settlement.allocations:
                allocation_row = db.get(BudgetAllocationRow, f"{child.id}:{allocation.dimension}")
                if allocation_row is None:
                    raise RecordNotFoundError(f"Budget allocation missing: {child.id}:{allocation.dimension}")
                allocation_row.version, allocation_row.payload_json = allocation.version, allocation.model_dump_json()
            self._append_budget_event(
                db, event_id=f"budget-result-accepted:{child.id}:{updated_parent.version}",
                parent=updated_parent, child_run_id=child.id,
                occurred_at=command.accepted_at, event_type="budget.consumed_released",
                usage=command.usage,
            )
            event = RunEvent(id=f"result-validated:{child.id}:{command.envelope_hash[:16]}", parent_run_id=parent.id, child_run_id=child.id, event_type="result.accepted", status="completed", occurred_at=command.accepted_at, payload={"task_id": task.id, "envelope_ref": command.envelope_ref, "result_hash": command.envelope_hash})
            db.add(self._event_row(self._assign_event_sequence(db, event)))
            return accepted

    def finalize_parent_merge(self, command: ParentMergeFinalization) -> ParentRun:
        """Atomically persist the canonical merge report, candidates and Parent terminal state."""
        with self._write_session() as db:
            parent_row = self._require_parent_row(db, command.parent_run_id)
            parent = ParentRun.model_validate_json(parent_row.payload_json)
            if parent.merge_report_id == command.report.id:
                existing = db.get(MergeReportRow, command.report.id)
                if existing is not None and existing.payload_json == command.report.model_dump_json():
                    return parent
                raise MergeConflictError("merge_conflict: merge report id changed")
            if parent.status in {"cancelling", "cancelled", "succeeded", "partial", "failed", "timed_out", "budget_exhausted"}:
                raise MergeConflictError("merge_conflict: parent is not mergeable")
            if parent.phase != "validating" or parent.version != command.expected_parent_version:
                raise MergeConflictError("merge_conflict: parent version changed")
            if command.report.parent_run_id != parent.id or command.report.result_version != parent.result_version + 1:
                raise MergeConflictError("merge_conflict: report version invalid")
            existing_report = db.get(MergeReportRow, command.report.id)
            if existing_report is not None:
                if existing_report.payload_json != command.report.model_dump_json():
                    raise MergeConflictError("merge_conflict: merge report payload changed")
            else:
                db.add(MergeReportRow(
                    id=command.report.id, parent_run_id=parent.id,
                    result_version=command.report.result_version,
                    created_at=command.report.created_at,
                    payload_json=command.report.model_dump_json(),
                ))
            for candidate in command.candidates:
                if candidate.parent_run_id != parent.id or candidate.expected_parent_version != parent.version:
                    raise MergeConflictError("merge_conflict: candidate parent/version mismatch")
                existing = db.scalar(select(CandidateMergeRow).where(
                    CandidateMergeRow.parent_run_id == parent.id,
                    CandidateMergeRow.idempotency_key == candidate.idempotency_key,
                ))
                if existing is not None:
                    if existing.payload_hash != candidate.payload_hash or existing.candidate_key != candidate.candidate_key:
                        raise MergeConflictError("merge_conflict: candidate idempotency conflict")
                    continue
                if db.scalar(select(CandidateMergeRow).where(
                    CandidateMergeRow.parent_run_id == parent.id,
                    CandidateMergeRow.candidate_key == candidate.candidate_key,
                )) is not None:
                    raise MergeConflictError("merge_conflict: candidate uniqueness conflict")
                db.add(CandidateMergeRow(
                    id=candidate.id, parent_run_id=parent.id, candidate_key=candidate.candidate_key,
                    idempotency_key=candidate.idempotency_key, payload_hash=candidate.payload_hash,
                    parent_version=parent.version + 1, created_at=candidate.created_at,
                    payload_json=candidate.model_dump_json(),
                ))
            updated = transition_run(parent, command.terminal_status, expected_version=parent.version, occurred_at=command.occurred_at)
            updated = ParentRun.model_validate({
                **updated.model_dump(mode="python"), "phase": "terminal",
                "result_version": command.report.result_version, "merge_report_id": command.report.id,
            })
            result = db.execute(update(ParentRunRow).where(
                ParentRunRow.id == parent.id, ParentRunRow.version == parent.version,
            ).values(
                status=updated.status,
                version=updated.version,
                updated_at=updated.updated_at,
                payload_json=updated.model_dump_json(),
            ))
            if result.rowcount != 1:
                raise MergeConflictError("merge_conflict: parent CAS failed")
            event = RunEvent(
                id=f"result-merged:{parent.id}:{command.report.final_output_hash[:16]}",
                parent_run_id=parent.id, event_type="result.merged", status="completed",
                occurred_at=command.occurred_at,
                payload={"merge_report_id": command.report.id, "result_version": command.report.result_version},
            )
            db.add(self._event_row(self._assign_event_sequence(db, event)))
            outbox = OutboxMessage(
                id=f"outbox:chat-backfill:{parent.id}:{command.report.result_version}",
                topic="chat.backfill_requested", aggregate_id=parent.id,
                idempotency_key=f"backfill:{parent.id}:{command.report.result_version}",
                payload={"result_version": command.report.result_version, "message_kind": "delegation.final"},
                created_at=command.occurred_at,
            )
            existing_outbox = db.scalar(select(OutboxRow).where(
                OutboxRow.idempotency_key == outbox.idempotency_key
            ))
            if existing_outbox is None:
                db.add(self._outbox_row(outbox))
            return updated

    def transition(
        self,
        run_id: str,
        target_status: RunStatus,
        *,
        expected_version: int,
        occurred_at: datetime,
    ) -> ParentRun | ChildRun:
        with self._write_session() as db:
            parent_row = db.get(ParentRunRow, run_id)
            row: ParentRunRow | ChildRunRow
            if parent_row is not None:
                current: ParentRun | ChildRun = ParentRun.model_validate_json(
                    parent_row.payload_json
                )
                row = parent_row
                parent_run_id = current.id
                child_run_id = None
            else:
                child_row = db.get(ChildRunRow, run_id)
                if child_row is None:
                    raise RecordNotFoundError(f"Run not found: {run_id}")
                current = ChildRun.model_validate_json(child_row.payload_json)
                row = child_row
                parent_run_id = current.parent_run_id
                child_run_id = current.id
                if current.lease_token is not None:
                    raise RevisionConflictError(
                        "active child leases require lease credentials for transitions"
                    )
            if current.version != expected_version:
                raise RevisionConflictError(
                    f"Run {run_id} expected version {expected_version}, "
                    f"found {current.version}"
                )
            updated_run = transition_run(
                current,
                target_status,
                expected_version=expected_version,
                occurred_at=occurred_at,
            )
            row.status = updated_run.status
            row.version = updated_run.version
            row.updated_at = updated_run.updated_at
            row.payload_json = updated_run.model_dump_json()
            if isinstance(row, ChildRunRow):
                row.lease_owner = updated_run.lease_owner
                row.lease_token = updated_run.lease_token
                row.lease_expires_at = updated_run.lease_expires_at
                row.heartbeat_at = updated_run.heartbeat_at
            status_event = RunEvent(
                id=f"run-status:{run_id}:{updated_run.version}",
                parent_run_id=parent_run_id,
                child_run_id=child_run_id,
                event_type="run.status_changed",
                status="completed",
                occurred_at=occurred_at,
                payload={
                    "from": current.status,
                    "to": updated_run.status,
                    "version": updated_run.version,
                },
            )
            status_event = self._assign_event_sequence(db, status_event)
            db.add(self._event_row(status_event))
            db.flush()
            return updated_run

    def claim_child_run(
        self,
        child_run_id: str,
        *,
        worker_id: str,
        lease_token: str,
        expected_version: int,
        claimed_at: datetime,
        lease_ttl: timedelta,
    ) -> ChildRun | None:
        if lease_ttl <= timedelta(0):
            raise ValueError("lease_ttl must be positive")
        with self._write_session() as db:
            row = db.get(ChildRunRow, child_run_id)
            if row is None:
                raise RecordNotFoundError(f"Child run not found: {child_run_id}")
            current = ChildRun.model_validate_json(row.payload_json)
            if (
                current.status != "queued"
                or current.version != expected_version
                or claimed_at >= current.deadline_at
            ):
                return None
            claimed = transition_run(
                current,
                "running",
                expected_version=expected_version,
                occurred_at=claimed_at,
            ).model_copy(
                update={
                    "lease_owner": worker_id,
                    "lease_token": lease_token,
                    "lease_expires_at": claimed_at + lease_ttl,
                    "heartbeat_at": claimed_at,
                }
            )
            claimed = ChildRun.model_validate(claimed.model_dump(mode="python"))
            result = db.execute(
                update(ChildRunRow)
                .where(
                    ChildRunRow.id == child_run_id,
                    ChildRunRow.status == "queued",
                    ChildRunRow.version == expected_version,
                )
                .values(
                    status=claimed.status,
                    version=claimed.version,
                    lease_owner=claimed.lease_owner,
                    lease_token=claimed.lease_token,
                    lease_expires_at=claimed.lease_expires_at,
                    heartbeat_at=claimed.heartbeat_at,
                    updated_at=claimed.updated_at,
                    payload_json=claimed.model_dump_json(),
                )
            )
            if result.rowcount != 1:
                return None
            self._append_status_event(
                db,
                current=current,
                updated=claimed,
                occurred_at=claimed_at,
            )
            db.flush()
            return claimed

    def heartbeat_child_run(
        self,
        child_run_id: str,
        *,
        worker_id: str,
        lease_token: str,
        expected_version: int,
        heartbeat_at: datetime,
        lease_ttl: timedelta,
    ) -> ChildRun:
        if lease_ttl <= timedelta(0):
            raise ValueError("lease_ttl must be positive")
        with self._write_session() as db:
            row = db.get(ChildRunRow, child_run_id)
            if row is None:
                raise RecordNotFoundError(f"Child run not found: {child_run_id}")
            current = ChildRun.model_validate_json(row.payload_json)
            if (
                current.status != "running"
                or current.version != expected_version
                or current.lease_owner != worker_id
                or current.lease_token != lease_token
                or current.lease_expires_at is None
                or heartbeat_at > current.lease_expires_at
                or heartbeat_at < current.heartbeat_at
                or heartbeat_at < current.updated_at
            ):
                raise RevisionConflictError(f"Child lease conflict: {child_run_id}")
            updated = ChildRun.model_validate(
                {
                    **current.model_dump(mode="python"),
                    "version": current.version + 1,
                    "heartbeat_at": heartbeat_at,
                    "lease_expires_at": heartbeat_at + lease_ttl,
                    "updated_at": heartbeat_at,
                }
            )
            result = db.execute(
                update(ChildRunRow)
                .where(
                    ChildRunRow.id == child_run_id,
                    ChildRunRow.version == expected_version,
                    ChildRunRow.lease_owner == worker_id,
                    ChildRunRow.lease_token == lease_token,
                )
                .values(
                    version=updated.version,
                    lease_expires_at=updated.lease_expires_at,
                    heartbeat_at=updated.heartbeat_at,
                    updated_at=updated.updated_at,
                    payload_json=updated.model_dump_json(),
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                raise RevisionConflictError(f"Child lease conflict: {child_run_id}")
            return updated

    def complete_child_run(
        self,
        child_run_id: str,
        *,
        target_status: Literal[
            "succeeded", "partial", "failed", "timed_out", "budget_exhausted"
        ],
        result_envelope_ref: str,
        result_hash: str,
        worker_id: str,
        lease_token: str,
        expected_version: int,
        completed_at: datetime,
        error_code: str | None = None,
    ) -> ChildRun:
        with self._write_session() as db:
            row = db.get(ChildRunRow, child_run_id)
            if row is None:
                raise RecordNotFoundError(f"Child run not found: {child_run_id}")
            current = ChildRun.model_validate_json(row.payload_json)
            if (
                current.status != "running"
                or current.version != expected_version
                or current.lease_owner != worker_id
                or current.lease_token != lease_token
                or current.lease_expires_at is None
                or completed_at > current.lease_expires_at
                or completed_at > current.deadline_at
            ):
                raise RevisionConflictError(f"Child lease conflict: {child_run_id}")
            updated = transition_run(
                current,
                target_status,
                expected_version=expected_version,
                occurred_at=completed_at,
            ).model_copy(
                update={
                    "result_envelope_ref": result_envelope_ref,
                    "result_hash": result_hash,
                    "error_code": error_code,
                }
            )
            updated = ChildRun.model_validate(updated.model_dump(mode="python"))
            result = db.execute(
                update(ChildRunRow)
                .where(
                    ChildRunRow.id == child_run_id,
                    ChildRunRow.status == "running",
                    ChildRunRow.version == expected_version,
                    ChildRunRow.lease_owner == worker_id,
                    ChildRunRow.lease_token == lease_token,
                    ChildRunRow.lease_expires_at >= completed_at,
                )
                .values(
                    status=updated.status,
                    version=updated.version,
                    lease_owner=None,
                    lease_token=None,
                    lease_expires_at=None,
                    heartbeat_at=None,
                    updated_at=updated.updated_at,
                    payload_json=updated.model_dump_json(),
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                raise RevisionConflictError(f"Child lease conflict: {child_run_id}")
            self._append_status_event(
                db,
                current=current,
                updated=updated,
                occurred_at=completed_at,
            )
            return updated

    def requeue_expired_child_lease(
        self,
        child_run_id: str,
        *,
        expected_version: int,
        recovered_at: datetime,
    ) -> ChildRun:
        with self._write_session() as db:
            row = db.get(ChildRunRow, child_run_id)
            if row is None:
                raise RecordNotFoundError(f"Child run not found: {child_run_id}")
            current = ChildRun.model_validate_json(row.payload_json)
            if (
                current.status != "running"
                or current.version != expected_version
                or current.lease_expires_at is None
                or recovered_at <= current.lease_expires_at
            ):
                raise RevisionConflictError(f"Child lease is not recoverable: {child_run_id}")
            updated = ChildRun.model_validate(
                {
                    **current.model_dump(mode="python"),
                    "status": "queued",
                    "version": current.version + 1,
                    "lease_owner": None,
                    "lease_token": None,
                    "lease_expires_at": None,
                    "heartbeat_at": None,
                    "updated_at": recovered_at,
                }
            )
            result = db.execute(
                update(ChildRunRow)
                .where(
                    ChildRunRow.id == child_run_id,
                    ChildRunRow.status == "running",
                    ChildRunRow.version == expected_version,
                    ChildRunRow.lease_token == current.lease_token,
                )
                .values(
                    status=updated.status,
                    version=updated.version,
                    lease_owner=None,
                    lease_token=None,
                    lease_expires_at=None,
                    heartbeat_at=None,
                    updated_at=updated.updated_at,
                    payload_json=updated.model_dump_json(),
                )
            )
            if result.rowcount != 1:
                raise RevisionConflictError(f"Child lease conflict: {child_run_id}")
            self._append_status_event(
                db,
                current=current,
                updated=updated,
                occurred_at=recovered_at,
            )
            return updated

    def append_event(self, run_event: RunEvent) -> RunEvent:
        with self._write_session() as db:
            self._require_parent_row(db, run_event.parent_run_id)
            self._validate_child_ownership(
                db, run_event.parent_run_id, run_event.child_run_id
            )
            existing = db.get(RunEventRow, run_event.id)
            payload_json = run_event.model_dump_json()
            if existing is not None:
                persisted = RunEvent.model_validate_json(existing.payload_json)
                candidate = run_event.model_copy(update={"event_seq": persisted.event_seq})
                if persisted == candidate:
                    return persisted
                raise IdempotencyPayloadConflictError(
                    f"Run event payload conflict: {run_event.id}"
                )
            run_event = self._assign_event_sequence(db, run_event)
            db.add(self._event_row(run_event))
            db.flush()
        return run_event

    def append_budget_event(
        self,
        *,
        id: str,
        parent_run_id: str,
        child_run_id: str | None,
        event_type: str,
        occurred_at: datetime,
        payload: dict[str, Any],
    ) -> RunEvent:
        """Append one explicit budget-ledger evidence record.

        Keep budget accounting in its existing transaction; this public helper
        is for durable/recoverable accounting operations that already have a
        committed ledger result.
        """
        if not event_type.startswith("budget."):
            raise ValueError("budget event_type required")
        return self.append_event(RunEvent(
            id=id, parent_run_id=parent_run_id, child_run_id=child_run_id,
            event_type=event_type, status="completed", occurred_at=occurred_at,
            payload=payload,
        ))

    def link_artifact(self, link: ArtifactLink) -> ArtifactLink:
        with self._write_session() as db:
            self._require_parent_row(db, link.parent_run_id)
            self._validate_child_ownership(db, link.parent_run_id, link.child_run_id)
            existing = db.get(ArtifactLinkRow, link.id)
            payload_json = link.model_dump_json()
            if existing is not None:
                if existing.payload_json == payload_json:
                    return ArtifactLink.model_validate_json(existing.payload_json)
                raise IdempotencyPayloadConflictError(
                    f"Artifact link payload conflict: {link.id}"
                )
            db.add(
                ArtifactLinkRow(
                    id=link.id,
                    parent_run_id=link.parent_run_id,
                    child_run_id=link.child_run_id,
                    artifact_ref=link.artifact_ref,
                    kind=link.kind,
                    restricted=link.restricted,
                    created_at=link.created_at,
                    payload_json=payload_json,
                )
            )
            db.flush()
        return link

    def save_merge_report(self, report: MergeReport) -> MergeReport:
        try:
            with self._write_session() as db:
                self._require_parent_row(db, report.parent_run_id)
                existing = db.get(MergeReportRow, report.id)
                payload_json = report.model_dump_json()
                if existing is not None:
                    if existing.payload_json == payload_json:
                        return MergeReport.model_validate_json(existing.payload_json)
                    raise IdempotencyPayloadConflictError(
                        f"Merge report payload conflict: {report.id}"
                    )
                db.add(
                    MergeReportRow(
                        id=report.id,
                        parent_run_id=report.parent_run_id,
                        result_version=report.result_version,
                        created_at=report.created_at,
                        payload_json=payload_json,
                    )
                )
                db.flush()
        except IntegrityError as exc:
            raise IdempotencyPayloadConflictError(
                "Parent result version already has a different merge report"
            ) from exc
        return report

    def stage_candidate_merge(self, write: CandidateMergeWrite) -> CandidateMergeWrite:
        """CAS-protect shared candidate writes; never overwrite a prior fact."""
        with self._write_session() as db:
            parent_row = self._require_parent_row(db, write.parent_run_id)
            parent = ParentRun.model_validate_json(parent_row.payload_json)
            if parent.status in {"cancelling", "cancelled", "succeeded", "partial", "failed", "timed_out", "budget_exhausted"}:
                raise MergeConflictError("merge_conflict: parent is not mergeable")
            existing = db.scalar(
                select(CandidateMergeRow).where(
                    CandidateMergeRow.parent_run_id == write.parent_run_id,
                    CandidateMergeRow.idempotency_key == write.idempotency_key,
                )
            )
            if existing is not None:
                persisted = CandidateMergeWrite.model_validate_json(existing.payload_json)
                if persisted.payload_hash == write.payload_hash and persisted.candidate_key == write.candidate_key:
                    return persisted
                raise MergeConflictError("merge_conflict: idempotency key has different payload")
            if parent.version != write.expected_parent_version:
                raise MergeConflictError("merge_conflict: parent version changed")
            same_key = db.scalar(
                select(CandidateMergeRow).where(
                    CandidateMergeRow.parent_run_id == write.parent_run_id,
                    CandidateMergeRow.candidate_key == write.candidate_key,
                )
            )
            if same_key is not None:
                raise MergeConflictError("merge_conflict: candidate already staged")
            updated_parent = ParentRun.model_validate({
                **parent.model_dump(mode="python"), "version": parent.version + 1,
                "updated_at": write.created_at,
            })
            result = db.execute(update(ParentRunRow).where(
                ParentRunRow.id == parent.id, ParentRunRow.version == write.expected_parent_version,
            ).values(version=updated_parent.version, updated_at=updated_parent.updated_at, payload_json=updated_parent.model_dump_json()))
            if result.rowcount != 1:
                raise MergeConflictError("merge_conflict: parent version changed")
            db.add(CandidateMergeRow(
                id=write.id, parent_run_id=write.parent_run_id, candidate_key=write.candidate_key,
                idempotency_key=write.idempotency_key, payload_hash=write.payload_hash,
                parent_version=updated_parent.version, created_at=write.created_at,
                payload_json=write.model_dump_json(),
            ))
            try:
                db.flush()
            except IntegrityError as exc:
                raise MergeConflictError("merge_conflict: candidate uniqueness conflict") from exc
            return write

    def enqueue_outbox(self, message: OutboxMessage) -> OutboxMessage:
        try:
            with self._write_session() as db:
                existing = db.scalar(
                    select(OutboxRow).where(
                        OutboxRow.idempotency_key == message.idempotency_key
                    )
                )
                if existing is not None:
                    if (
                        existing.payload_hash == message.payload_hash
                        and existing.topic == message.topic
                        and existing.aggregate_id == message.aggregate_id
                    ):
                        return OutboxMessage.model_validate_json(existing.payload_json)
                    raise IdempotencyPayloadConflictError(
                        "Outbox idempotency key is bound to a different payload"
                    )
                db.add(
                    OutboxRow(
                        id=message.id,
                        topic=message.topic,
                        aggregate_id=message.aggregate_id,
                        idempotency_key=message.idempotency_key,
                        payload_hash=message.payload_hash,
                        status=message.status,
                        created_at=message.created_at,
                        payload_json=message.model_dump_json(),
                    )
                )
                db.flush()
        except IntegrityError as exc:
            raise IdempotencyPayloadConflictError(
                "Outbox idempotency key is bound to a different payload"
            ) from exc
        return message

    def list_events(
        self,
        parent_run_id: str,
        *,
        after_seq: int = 0,
        limit: int = 100,
    ) -> Page[RunEvent]:
        if after_seq < 0:
            raise ValueError("after_seq cannot be negative")
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with Session(self.engine) as db:
            self._require_parent_row(db, parent_run_id)
            rows = db.scalars(
                select(RunEventRow)
                .where(
                    RunEventRow.parent_run_id == parent_run_id,
                    RunEventRow.event_seq > after_seq,
                )
                .order_by(RunEventRow.event_seq)
                .limit(limit + 1)
            ).all()
            selected = rows[:limit]
            return Page(
                items=tuple(
                    RunEvent.model_validate_json(row.payload_json) for row in selected
                ),
                next_cursor=(
                    str(selected[-1].event_seq) if len(rows) > limit and selected else None
                ),
            )

    def list_outbox(
        self,
        *,
        limit: int = 100,
        cursor: str | None = None,
    ) -> Page[OutboxMessage]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with Session(self.engine) as db:
            statement = select(OutboxRow)
            if cursor is not None:
                cursor_row = db.get(OutboxRow, cursor)
                if cursor_row is None:
                    raise RecordNotFoundError(f"Outbox cursor not found: {cursor}")
                statement = statement.where(
                    or_(
                        OutboxRow.created_at > cursor_row.created_at,
                        and_(
                            OutboxRow.created_at == cursor_row.created_at,
                            OutboxRow.id > cursor_row.id,
                        ),
                    )
                )
            rows = db.scalars(
                statement.order_by(OutboxRow.created_at, OutboxRow.id).limit(limit + 1)
            ).all()
            has_more = len(rows) > limit
            selected = rows[:limit]
            return Page(
                items=tuple(
                    OutboxMessage.model_validate_json(row.payload_json) for row in selected
                ),
                next_cursor=selected[-1].id if has_more and selected else None,
            )

    def mark_outbox_delivered(
        self, message_id: str, *, delivered_at: datetime
    ) -> OutboxMessage:
        with self._write_session() as db:
            row = db.get(OutboxRow, message_id)
            if row is None:
                raise RecordNotFoundError(f"Outbox message not found: {message_id}")
            message = OutboxMessage.model_validate_json(row.payload_json)
            if message.status == "delivered":
                return message
            updated = message.model_copy(update={
                "status": "delivered", "attempts": message.attempts + 1,
                "delivered_at": delivered_at,
            })
            row.status, row.payload_json = updated.status, updated.model_dump_json()
            return updated

    def settle_child_budget(
        self,
        child_run_id: str,
        usage: BudgetUsage,
        *,
        expected_parent_version: int,
        settled_at: datetime,
    ) -> BudgetSettlementResult:
        emitted: list[RunEvent] = []
        with self._write_session() as db:
            child_row = db.get(ChildRunRow, child_run_id)
            if child_row is None:
                raise RecordNotFoundError(f"Child run not found: {child_run_id}")
            parent_row = self._require_parent_row(db, child_row.parent_run_id)
            if parent_row.version != expected_parent_version:
                raise RevisionConflictError(
                    f"Parent {parent_row.id} expected version "
                    f"{expected_parent_version}, found {parent_row.version}"
                )
            parent = ParentRun.model_validate_json(parent_row.payload_json)
            allocations = self._allocations_for_run(db, child_run_id)
            settlement = settle_budget(
                parent_reserved=parent.budget_reserved,
                parent_consumed=parent.budget_consumed,
                allocations=allocations,
                usage=usage,
            )
            updated_parent = ParentRun.model_validate(
                {
                    **parent.model_dump(mode="python"),
                    "budget_reserved": settlement.updated_reserved,
                    "budget_consumed": settlement.updated_consumed,
                    "version": parent.version + 1,
                    "updated_at": settled_at,
                }
            )
            result = db.execute(
                update(ParentRunRow)
                .where(
                    ParentRunRow.id == parent.id,
                    ParentRunRow.version == expected_parent_version,
                )
                .values(
                    version=updated_parent.version,
                    updated_at=updated_parent.updated_at,
                    payload_json=updated_parent.model_dump_json(),
                )
            )
            if result.rowcount != 1:
                raise RevisionConflictError(f"Parent revision conflict: {parent.id}")
            for allocation in settlement.allocations:
                allocation_row = db.get(
                    BudgetAllocationRow,
                    f"{child_run_id}:{allocation.dimension}",
                )
                if allocation_row is None:
                    raise RecordNotFoundError(
                        f"Budget allocation missing: {child_run_id}:{allocation.dimension}"
                    )
                allocation_row.version = allocation.version
                allocation_row.payload_json = allocation.model_dump_json()
            budget_event = RunEvent(
                id=f"budget-settled:{child_run_id}:{updated_parent.version}",
                parent_run_id=parent.id, child_run_id=child_run_id,
                event_type="budget.consumed_released", status="completed", occurred_at=settled_at,
                payload={"usage": usage.model_dump(mode="json"), "budget_reserved": updated_parent.budget_reserved.model_dump(mode="json"), "budget_consumed": updated_parent.budget_consumed.model_dump(mode="json")},
            )
            budget_event = self._assign_event_sequence(db, budget_event)
            db.add(self._event_row(budget_event))
            emitted.append(budget_event)
            db.flush()
            result_value = BudgetSettlementResult(
                parent=updated_parent,
                allocations=settlement.allocations,
            )
        return result_value

    def get_run_tree(self, parent_run_id: str) -> RunTree:
        with Session(self.engine) as db:
            parent_row = self._require_parent_row(db, parent_run_id)
            task_rows = db.scalars(
                select(ChildTaskRow)
                .where(ChildTaskRow.parent_run_id == parent_run_id)
                .order_by(ChildTaskRow.created_at, ChildTaskRow.id)
            ).all()
            run_rows = db.scalars(
                select(ChildRunRow)
                .where(ChildRunRow.parent_run_id == parent_run_id)
                .order_by(ChildRunRow.created_at, ChildRunRow.id)
            ).all()
            allocation_rows = db.scalars(
                select(BudgetAllocationRow)
                .where(BudgetAllocationRow.parent_run_id == parent_run_id)
                .order_by(BudgetAllocationRow.child_run_id, BudgetAllocationRow.dimension)
            ).all()
            event_rows = db.scalars(
                select(RunEventRow)
                .where(RunEventRow.parent_run_id == parent_run_id)
                .order_by(RunEventRow.event_seq)
            ).all()
            artifact_rows = db.scalars(
                select(ArtifactLinkRow)
                .where(ArtifactLinkRow.parent_run_id == parent_run_id)
                .order_by(ArtifactLinkRow.created_at, ArtifactLinkRow.id)
            ).all()
            report_rows = db.scalars(
                select(MergeReportRow)
                .where(MergeReportRow.parent_run_id == parent_run_id)
                .order_by(MergeReportRow.result_version, MergeReportRow.id)
            ).all()
            return RunTree(
                parent=ParentRun.model_validate_json(parent_row.payload_json),
                child_tasks=tuple(
                    ChildTask.model_validate_json(row.payload_json) for row in task_rows
                ),
                child_runs=tuple(
                    ChildRun.model_validate_json(row.payload_json) for row in run_rows
                ),
                allocations=tuple(
                    BudgetAllocation.model_validate_json(row.payload_json)
                    for row in allocation_rows
                ),
                events=tuple(
                    RunEvent.model_validate_json(row.payload_json) for row in event_rows
                ),
                artifact_links=tuple(
                    ArtifactLink.model_validate_json(row.payload_json)
                    for row in artifact_rows
                ),
                merge_reports=tuple(
                    MergeReport.model_validate_json(row.payload_json)
                    for row in report_rows
                ),
            )

    def list_parent_run_ids(self, *, limit: int = 500) -> tuple[str, ...]:
        if not 1 <= limit <= 1000:
            raise ValueError("parent run limit out of range")
        with Session(self.engine) as db:
            return tuple(db.scalars(
                select(ParentRunRow.id).order_by(ParentRunRow.updated_at.desc()).limit(limit)
            ).all())

    def list_parent_run_ids_page(
        self, *, after_id: str | None = None, limit: int = 500
    ) -> Page[str]:
        """List every parent deterministically for restart-time projections."""
        if not 1 <= limit <= 1000:
            raise ValueError("parent run limit out of range")
        with Session(self.engine) as db:
            statement = select(ParentRunRow.id)
            if after_id is not None:
                statement = statement.where(ParentRunRow.id > after_id)
            selected = tuple(db.scalars(
                statement.order_by(ParentRunRow.id.asc()).limit(limit + 1)
            ).all())
        has_more = len(selected) > limit
        items = selected[:limit]
        return Page(items=items, next_cursor=items[-1] if has_more and items else None)

    def _require_parent_row(self, db: Session, parent_run_id: str) -> ParentRunRow:
        row = db.get(ParentRunRow, parent_run_id)
        if row is None:
            raise RecordNotFoundError(f"Parent run not found: {parent_run_id}")
        return row

    @staticmethod
    def _validate_child_ownership(
        db: Session,
        parent_run_id: str,
        child_run_id: str | None,
    ) -> None:
        if child_run_id is None:
            return
        child_row = db.get(ChildRunRow, child_run_id)
        if child_row is None:
            raise RecordNotFoundError(f"Child run not found: {child_run_id}")
        if child_row.parent_run_id != parent_run_id:
            raise RunStoreError(
                f"Child run {child_run_id} does not belong to parent {parent_run_id}",
                code="child_parent_mismatch",
            )

    @staticmethod
    def _assign_event_sequence(db: Session, run_event: RunEvent) -> RunEvent:
        if run_event.event_seq is not None:
            raise ValueError("event_seq is assigned by the run store")
        parent_row = db.get(ParentRunRow, run_event.parent_run_id)
        if parent_row is None:
            raise RecordNotFoundError(
                f"Parent run not found: {run_event.parent_run_id}"
            )
        sequence = parent_row.next_event_seq
        parent_row.next_event_seq = sequence + 1
        return run_event.model_copy(update={"event_seq": sequence})

    def _append_status_event(
        self,
        db: Session,
        *,
        current: ParentRun | ChildRun,
        updated: ParentRun | ChildRun,
        occurred_at: datetime,
    ) -> None:
        status_event = RunEvent(
            id=f"run-status:{updated.id}:{updated.version}",
            parent_run_id=(
                updated.id if isinstance(updated, ParentRun) else updated.parent_run_id
            ),
            child_run_id=None if isinstance(updated, ParentRun) else updated.id,
            event_type="run.status_changed",
            status="completed",
            occurred_at=occurred_at,
            payload={
                "from": current.status,
                "to": updated.status,
                "version": updated.version,
            },
        )
        status_event = self._assign_event_sequence(db, status_event)
        db.add(self._event_row(status_event))

    def _append_budget_event(
        self,
        db: Session,
        *,
        event_id: str,
        parent: ParentRun,
        child_run_id: str,
        occurred_at: datetime,
        event_type: str,
        usage: BudgetUsage,
    ) -> None:
        event = RunEvent(
            id=event_id, parent_run_id=parent.id, child_run_id=child_run_id,
            event_type=event_type, status="completed", occurred_at=occurred_at,
            payload={
                "usage": usage.model_dump(mode="json"),
                "budget_reserved": parent.budget_reserved.model_dump(mode="json"),
                "budget_consumed": parent.budget_consumed.model_dump(mode="json"),
            },
        )
        db.add(self._event_row(self._assign_event_sequence(db, event)))

    def _allocations_for_run(
        self,
        db: Session,
        child_run_id: str,
    ) -> tuple[BudgetAllocation, ...]:
        rows = db.scalars(
            select(BudgetAllocationRow)
            .where(BudgetAllocationRow.child_run_id == child_run_id)
            .order_by(BudgetAllocationRow.id)
        ).all()
        return tuple(
            BudgetAllocation.model_validate_json(row.payload_json) for row in rows
        )

    @staticmethod
    def _parent_row(parent: ParentRun) -> ParentRunRow:
        return ParentRunRow(
            id=parent.id,
            status=parent.status,
            version=parent.version,
            next_event_seq=1,
            created_at=parent.created_at,
            updated_at=parent.updated_at,
            payload_json=parent.model_dump_json(),
        )

    @staticmethod
    def _task_row(task: ChildTask) -> ChildTaskRow:
        return ChildTaskRow(
            id=task.id,
            parent_run_id=task.parent_run_id,
            specialist_id=task.specialist_id,
            status=task.status,
            version=task.version,
            idempotency_key=task.idempotency_key,
            accepted_child_run_id=task.accepted_child_run_id,
            contract_hash=task.contract_hash,
            created_at=task.created_at,
            payload_json=task.model_dump_json(),
        )

    @staticmethod
    def _child_run_row(child_run: ChildRun) -> ChildRunRow:
        return ChildRunRow(
            id=child_run.id,
            child_task_id=child_run.child_task_id,
            parent_run_id=child_run.parent_run_id,
            attempt=child_run.attempt,
            status=child_run.status,
            version=child_run.version,
            lease_owner=child_run.lease_owner,
            lease_token=child_run.lease_token,
            lease_expires_at=child_run.lease_expires_at,
            heartbeat_at=child_run.heartbeat_at,
            created_at=child_run.created_at,
            updated_at=child_run.updated_at,
            payload_json=child_run.model_dump_json(),
        )

    @staticmethod
    def _event_row(run_event: RunEvent) -> RunEventRow:
        payload_json = run_event.model_dump_json()
        return RunEventRow(
            id=run_event.id,
            parent_run_id=run_event.parent_run_id,
            child_run_id=run_event.child_run_id,
            event_seq=run_event.event_seq,
            event_type=run_event.event_type,
            occurred_at=run_event.occurred_at,
            payload_hash=canonical_json_sha256(run_event.model_dump(mode="json")),
            payload_json=payload_json,
        )

    @staticmethod
    def _outbox_row(message: OutboxMessage) -> OutboxRow:
        return OutboxRow(
            id=message.id,
            topic=message.topic,
            aggregate_id=message.aggregate_id,
            idempotency_key=message.idempotency_key,
            payload_hash=message.payload_hash,
            status=message.status,
            created_at=message.created_at,
            payload_json=message.model_dump_json(),
        )
