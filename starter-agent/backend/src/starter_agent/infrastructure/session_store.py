from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, create_engine, delete, func, inspect, select, text, update
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from starter_agent.domain.models import (
    MemoryItem,
    Message,
    StoredContextSummary,
    StoredHistoryMessage,
    StoredMessage,
    StoredSessionSummary,
    TokenUsage,
)
from starter_agent.job_research.selection import PendingJobCandidate


class Base(DeclarativeBase):
    pass


class SessionRow(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MessageRow(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    turn_id: Mapped[str] = mapped_column(String(36), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tool_calls_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TurnUsageRow(Base):
    __tablename__ = "turn_usage"

    turn_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    provider: Mapped[str] = mapped_column(String(120))
    model: Mapped[str] = mapped_column(String(240))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ContextSummaryRow(Base):
    __tablename__ = "context_summaries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    content: Mapped[str] = mapped_column(Text)
    source_message_ids_json: Mapped[str] = mapped_column(Text)
    compacted_message_ids_json: Mapped[str] = mapped_column(Text)
    before_tokens: Mapped[int] = mapped_column(Integer)
    after_tokens: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TokenCalibrationRow(Base):
    __tablename__ = "token_calibration_profiles"

    profile_key: Mapped[str] = mapped_column(String(400), primary_key=True)
    provider: Mapped[str] = mapped_column(String(120), index=True)
    model: Mapped[str] = mapped_column(String(240), index=True)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    ratios_json: Mapped[str] = mapped_column(Text, default="[]")
    log_coefficient: Mapped[float] = mapped_column(Float, default=0.0)
    safe_coefficient: Mapped[float] = mapped_column(Float, default=1.0)
    last_raw_estimate: Mapped[int] = mapped_column(Integer, default=0)
    last_actual_prompt: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ToolArtifactRow(Base):
    __tablename__ = "tool_artifacts"

    source_ref: Mapped[str] = mapped_column(String(500), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    turn_id: Mapped[str] = mapped_column(String(36), index=True)
    tool_name: Mapped[str] = mapped_column(String(120))
    call_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    server_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    snapshot_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    schema_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_content_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    truncation_summary_json: Mapped[str] = mapped_column(Text, default="{}")
    restricted: Mapped[int] = mapped_column(Integer, default=1)
    parent_run_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    child_task_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    child_run_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    policy_decision_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    approval_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    access_level: Mapped[str] = mapped_column(String(80), default="restricted")
    principal: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MemoryItemRow(Base):
    __tablename__ = "memory_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    key: Mapped[str] = mapped_column(String(120), index=True)
    value: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(40), index=True)
    source_ref: Mapped[str] = mapped_column(String(500))
    source_type: Mapped[str] = mapped_column(String(40))
    confidence: Mapped[float] = mapped_column(Float)
    verified_by: Mapped[str] = mapped_column(String(40))
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sensitivity: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class JobDescriptionApprovalRow(Base):
    __tablename__ = "job_description_ingestion_approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    principal: Mapped[str] = mapped_column(String(200), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    turn_id: Mapped[str] = mapped_column(String(36), index=True)
    call_id: Mapped[str] = mapped_column(String(160))
    artifact_ref: Mapped[str] = mapped_column(String(500), unique=True)
    server_id: Mapped[str] = mapped_column(String(160))
    snapshot_id: Mapped[str] = mapped_column(String(160))
    schema_hash: Mapped[str] = mapped_column(String(64))
    source_url: Mapped[str] = mapped_column(Text)
    source_content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    artifact_content_sha256: Mapped[str] = mapped_column(String(64))
    gate_reason_code: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class JobResearchCandidateRow(Base):
    __tablename__ = "job_research_candidates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    turn_id: Mapped[str] = mapped_column(String(36), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    company: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str] = mapped_column(Text, default="")
    source_url: Mapped[str] = mapped_column(Text, default="")
    evidence_level: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(32), index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class SQLiteSessionStore:
    def __init__(self, database_url: str, project_root: Path):
        if database_url.startswith("sqlite:///"):
            relative = database_url.removeprefix("sqlite:///")
            db_path = Path(relative)
            if not db_path.is_absolute():
                db_path = project_root / db_path
            db_path.parent.mkdir(parents=True, exist_ok=True)
            database_url = f"sqlite:///{db_path}"
        self.engine = create_engine(database_url)
        Base.metadata.create_all(self.engine)
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        """Apply small additive migrations required by existing local databases."""
        columns = {column["name"] for column in inspect(self.engine).get_columns("messages")}
        if "tool_calls_json" not in columns:
            with self.engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE messages ADD COLUMN "
                        "tool_calls_json TEXT NOT NULL DEFAULT '[]'"
                    )
                )
        artifact_columns = {
            column["name"]
            for column in inspect(self.engine).get_columns("tool_artifacts")
        }
        artifact_additions = {
            "server_id": "VARCHAR(160)",
            "call_id": "VARCHAR(160)",
            "snapshot_id": "VARCHAR(160)",
            "schema_hash": "VARCHAR(64)",
            "requested_url": "TEXT",
            "final_url": "TEXT",
            "source_url": "TEXT",
            "content_sha256": "VARCHAR(64)",
            "source_content_sha256": "VARCHAR(64)",
            "truncation_summary_json": "TEXT NOT NULL DEFAULT '{}'",
            "restricted": "INTEGER NOT NULL DEFAULT 1",
            "parent_run_id": "VARCHAR(160)",
            "child_task_id": "VARCHAR(160)",
            "child_run_id": "VARCHAR(160)",
            "policy_decision_id": "VARCHAR(160)",
            "approval_id": "VARCHAR(160)",
            "access_level": "VARCHAR(80) NOT NULL DEFAULT 'restricted'",
            "principal": "VARCHAR(200)",
            "expires_at": "DATETIME",
        }
        with self.engine.begin() as connection:
            for name, definition in artifact_additions.items():
                if name not in artifact_columns:
                    connection.execute(
                        text(
                            f"ALTER TABLE tool_artifacts ADD COLUMN {name} {definition}"
                        )
                    )

    def create_session(self) -> UUID:
        session_id = uuid4()
        now = datetime.now(UTC)
        with Session(self.engine) as db:
            db.add(SessionRow(id=str(session_id), created_at=now, updated_at=now))
            db.commit()
        return session_id

    def replace_pending_job_candidates(
        self,
        *,
        session_id: UUID,
        turn_id: UUID,
        candidates: Sequence[Mapping[str, Any]],
        expires_at: datetime,
    ) -> tuple[PendingJobCandidate, ...]:
        now = datetime.now(UTC)
        rows: list[JobResearchCandidateRow] = []
        with Session(self.engine) as db:
            db.execute(
                update(JobResearchCandidateRow)
                .where(
                    JobResearchCandidateRow.session_id == str(session_id),
                    JobResearchCandidateRow.status == "PENDING_CONFIRMATION",
                )
                .values(status="EXPIRED")
            )
            for ordinal, candidate in enumerate(candidates, start=1):
                candidate_id = uuid4()
                payload = dict(candidate)
                evidence_level = str(payload.get("evidence_level") or "complete")
                if evidence_level not in {"complete", "partial"}:
                    evidence_level = "partial"
                row = JobResearchCandidateRow(
                    id=str(candidate_id),
                    session_id=str(session_id),
                    turn_id=str(turn_id),
                    ordinal=ordinal,
                    title=str(payload.get("title") or payload.get("job_title") or ""),
                    company=str(payload.get("company") or ""),
                    location=str(payload.get("location") or ""),
                    source_url=str(payload.get("source_url") or payload.get("url") or ""),
                    evidence_level=evidence_level,
                    status="PENDING_CONFIRMATION",
                    payload_json=json.dumps(payload, ensure_ascii=False),
                    created_at=now,
                    expires_at=expires_at,
                )
                db.add(row)
                rows.append(row)
            session = db.get(SessionRow, str(session_id))
            if session is not None:
                session.updated_at = now
            db.flush()
            stored = tuple(self._pending_job_candidate(row) for row in rows)
            db.commit()
        return stored

    def resolve_pending_job_candidate(
        self,
        session_id: UUID,
        *,
        ordinal: int | None = None,
        candidate_id: UUID | None = None,
        now: datetime | None = None,
    ) -> PendingJobCandidate | None:
        if ordinal is None and candidate_id is None:
            return None
        current = now or datetime.now(UTC)
        with Session(self.engine) as db:
            statement = select(JobResearchCandidateRow).where(
                JobResearchCandidateRow.session_id == str(session_id),
                JobResearchCandidateRow.status == "PENDING_CONFIRMATION",
                JobResearchCandidateRow.expires_at > current,
            )
            if candidate_id is not None:
                statement = statement.where(
                    JobResearchCandidateRow.id == str(candidate_id)
                )
            else:
                statement = statement.where(JobResearchCandidateRow.ordinal == ordinal)
            row = db.scalar(statement.order_by(JobResearchCandidateRow.created_at.desc()))
        return None if row is None else self._pending_job_candidate(row)

    def list_pending_job_candidates(
        self,
        session_id: UUID,
        *,
        now: datetime | None = None,
    ) -> tuple[PendingJobCandidate, ...]:
        current = now or datetime.now(UTC)
        with Session(self.engine) as db:
            rows = tuple(
                db.scalars(
                    select(JobResearchCandidateRow)
                    .where(
                        JobResearchCandidateRow.session_id == str(session_id),
                        JobResearchCandidateRow.status == "PENDING_CONFIRMATION",
                        JobResearchCandidateRow.expires_at > current,
                    )
                    .order_by(JobResearchCandidateRow.ordinal)
                )
            )
        return tuple(self._pending_job_candidate(row) for row in rows)

    @staticmethod
    def _pending_job_candidate(row: JobResearchCandidateRow) -> PendingJobCandidate:
        return PendingJobCandidate(
            candidate_id=UUID(row.id),
            session_id=UUID(row.session_id),
            turn_id=UUID(row.turn_id),
            ordinal=row.ordinal,
            title=row.title,
            company=row.company,
            location=row.location,
            source_url=row.source_url,
            evidence_level=row.evidence_level,  # type: ignore[arg-type]
            status=row.status,  # type: ignore[arg-type]
            payload=json.loads(row.payload_json),
            created_at=row.created_at,
            expires_at=row.expires_at,
        )

    @staticmethod
    def _memory_item(row: MemoryItemRow, now: datetime | None = None) -> MemoryItem:
        current = now or datetime.now(UTC)
        expires_at = row.expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        status = row.status
        if status == "active" and expires_at is not None and expires_at <= current:
            status = "expired"
        return MemoryItem(
            id=UUID(row.id),
            key=row.key,
            value=row.value,
            category=row.category,  # type: ignore[arg-type]
            source_ref=row.source_ref,
            source_type=row.source_type,  # type: ignore[arg-type]
            confidence=row.confidence,
            verified_by=row.verified_by,  # type: ignore[arg-type]
            expires_at=expires_at,
            sensitivity=row.sensitivity,  # type: ignore[arg-type]
            status=status,  # type: ignore[arg-type]
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def create_memory(
        self,
        *,
        key: str,
        value: str,
        category: str,
        source_ref: str,
        source_type: str,
        confidence: float,
        verified_by: str,
        expires_at: datetime | None,
        sensitivity: str,
    ) -> MemoryItem:
        now = datetime.now(UTC)
        row = MemoryItemRow(
            id=str(uuid4()),
            key=key,
            value=value,
            category=category,
            source_ref=source_ref,
            source_type=source_type,
            confidence=confidence,
            verified_by=verified_by,
            expires_at=expires_at,
            sensitivity=sensitivity,
            status="active",
            created_at=now,
            updated_at=now,
        )
        with Session(self.engine) as db:
            db.add(row)
            db.commit()
            db.refresh(row)
        return self._memory_item(row, now)

    def upsert_inferred_memory(
        self,
        *,
        key: str,
        value: str,
        category: str,
        source_ref: str,
        confidence: float,
        expires_at: datetime,
        sensitivity: str,
    ) -> tuple[MemoryItem, str]:
        """Insert/update model-curated memory without overriding user decisions."""
        now = datetime.now(UTC)
        with Session(self.engine) as db:
            existing = db.scalar(
                select(MemoryItemRow)
                .where(MemoryItemRow.key == key)
                .order_by(MemoryItemRow.updated_at.desc())
                .limit(1)
            )
            if existing is not None and (
                existing.source_type in {"user_confirmed", "local_file"}
                or existing.status == "disabled"
            ):
                return self._memory_item(existing, now), "preserved"
            if existing is None:
                existing = MemoryItemRow(
                    id=str(uuid4()),
                    key=key,
                    value=value,
                    category=category,
                    source_ref=source_ref,
                    source_type="conversation_inferred",
                    confidence=confidence,
                    verified_by="memory_model",
                    expires_at=expires_at,
                    sensitivity=sensitivity,
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
                db.add(existing)
                action = "created"
            else:
                existing.value = value
                existing.category = category
                existing.source_ref = source_ref
                existing.source_type = "conversation_inferred"
                existing.confidence = confidence
                existing.verified_by = "memory_model"
                existing.expires_at = expires_at
                existing.sensitivity = sensitivity
                existing.status = "active"
                existing.updated_at = now
                action = "updated"
            db.commit()
            db.refresh(existing)
            return self._memory_item(existing, now), action

    def list_memories(
        self, *, active_only: bool = False, limit: int = 100
    ) -> list[MemoryItem]:
        now = datetime.now(UTC)
        with Session(self.engine) as db:
            rows = list(
                db.scalars(
                    select(MemoryItemRow)
                    .order_by(MemoryItemRow.updated_at.desc())
                    .limit(limit)
                )
            )
            changed = False
            for row in rows:
                expires_at = row.expires_at
                if expires_at is not None and expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=UTC)
                if row.status == "active" and expires_at and expires_at <= now:
                    row.status = "expired"
                    row.updated_at = now
                    changed = True
            if changed:
                db.commit()
            items = [self._memory_item(row, now) for row in rows]
        return [item for item in items if item.status == "active"] if active_only else items

    def get_memory(self, memory_id: UUID) -> MemoryItem | None:
        with Session(self.engine) as db:
            row = db.get(MemoryItemRow, str(memory_id))
            return self._memory_item(row) if row else None

    def update_memory(
        self,
        memory_id: UUID,
        *,
        key: str,
        value: str,
        category: str,
        source_ref: str,
        confidence: float,
        expires_at: datetime | None,
        sensitivity: str,
        status: str,
    ) -> MemoryItem | None:
        now = datetime.now(UTC)
        with Session(self.engine) as db:
            row = db.get(MemoryItemRow, str(memory_id))
            if row is None:
                return None
            row.key = key
            row.value = value
            row.category = category
            row.source_ref = source_ref
            row.source_type = "user_confirmed"
            row.confidence = confidence
            row.verified_by = "user"
            row.expires_at = expires_at
            row.sensitivity = sensitivity
            row.status = status
            row.updated_at = now
            db.commit()
            db.refresh(row)
            return self._memory_item(row, now)

    def delete_memory(self, memory_id: UUID) -> bool:
        with Session(self.engine) as db:
            row = db.get(MemoryItemRow, str(memory_id))
            if row is None:
                return False
            db.delete(row)
            db.commit()
            return True

    def ensure_session(self, session_id: UUID | None) -> UUID:
        if session_id is None:
            return self.create_session()
        with Session(self.engine) as db:
            exists = db.get(SessionRow, str(session_id))
            if exists is None:
                now = datetime.now(UTC)
                db.add(
                    SessionRow(
                        id=str(session_id), created_at=now, updated_at=now
                    )
                )
                db.commit()
        return session_id

    def add_message(
        self,
        session_id: UUID,
        turn_id: UUID,
        message: Message,
        *,
        message_id: UUID | None = None,
    ) -> UUID:
        """Persist a message, optionally using a caller-owned idempotency id."""
        now = datetime.now(UTC)
        message_id = message_id or uuid4()
        with Session(self.engine) as db:
            existing = db.get(MessageRow, str(message_id))
            if existing is not None:
                if (
                    existing.session_id == str(session_id)
                    and existing.turn_id == str(turn_id)
                    and existing.role == message.role
                    and existing.content == message.content
                ):
                    return message_id
                raise ValueError("message idempotency key is bound to different content")
            db.add(
                MessageRow(
                    id=str(message_id),
                    session_id=str(session_id),
                    turn_id=str(turn_id),
                    role=message.role,
                    content=message.content,
                    name=message.name,
                    tool_call_id=message.tool_call_id,
                    tool_calls_json=json.dumps(
                        [call.model_dump(mode="json") for call in message.tool_calls],
                        ensure_ascii=False,
                    ),
                    created_at=now,
                )
            )
            session = db.get(SessionRow, str(session_id))
            if session:
                session.updated_at = now
            db.commit()
        return message_id

    def record_usage(
        self,
        session_id: UUID,
        turn_id: UUID,
        provider: str,
        model: str,
        usage: TokenUsage,
    ) -> None:
        with Session(self.engine) as db:
            db.merge(
                TurnUsageRow(
                    turn_id=str(turn_id),
                    session_id=str(session_id),
                    provider=provider,
                    model=model,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    total_tokens=usage.total_tokens,
                    created_at=datetime.now(UTC),
                )
            )
            db.commit()

    def session_usage(self, session_id: UUID) -> TokenUsage:
        with Session(self.engine) as db:
            row = db.execute(
                select(
                    func.coalesce(func.sum(TurnUsageRow.prompt_tokens), 0),
                    func.coalesce(func.sum(TurnUsageRow.completion_tokens), 0),
                    func.coalesce(func.sum(TurnUsageRow.total_tokens), 0),
                ).where(TurnUsageRow.session_id == str(session_id))
            ).one()
        return TokenUsage(
            prompt_tokens=int(row[0]),
            completion_tokens=int(row[1]),
            total_tokens=int(row[2]),
        )

    def list_messages(self, session_id: UUID, limit: int = 50) -> list[Message]:
        with Session(self.engine) as db:
            rows = list(
                db.scalars(
                    select(MessageRow)
                    .where(MessageRow.session_id == str(session_id))
                    .order_by(MessageRow.created_at.desc())
                    .limit(limit)
                )
            )
        rows.reverse()
        return [
            Message(
                role=row.role,  # type: ignore[arg-type]
                content=row.content,
                name=row.name,
                tool_call_id=row.tool_call_id,
                tool_calls=json.loads(row.tool_calls_json or "[]"),
            )
            for row in rows
        ]

    def list_stored_messages(
        self, session_id: UUID, limit: int = 500
    ) -> list[StoredMessage]:
        with Session(self.engine) as db:
            rows = list(
                db.scalars(
                    select(MessageRow)
                    .where(MessageRow.session_id == str(session_id))
                    .order_by(MessageRow.created_at.desc())
                    .limit(limit)
                )
            )
        rows.reverse()
        return [
            StoredMessage(
                id=UUID(row.id),
                session_id=UUID(row.session_id),
                turn_id=UUID(row.turn_id),
                message=Message(
                    role=row.role,  # type: ignore[arg-type]
                    content=row.content,
                    name=row.name,
                    tool_call_id=row.tool_call_id,
                    tool_calls=json.loads(row.tool_calls_json or "[]"),
                ),
                created_at=row.created_at,
            )
            for row in rows
        ]

    def save_context_summary(
        self,
        session_id: UUID,
        content: str,
        source_message_ids: list[UUID],
        compacted_message_ids: list[UUID],
        before_tokens: int,
        after_tokens: int,
    ) -> StoredContextSummary:
        summary = StoredContextSummary(
            id=uuid4(),
            session_id=session_id,
            content=content,
            source_message_ids=source_message_ids,
            compacted_message_ids=compacted_message_ids,
            before_tokens=before_tokens,
            after_tokens=after_tokens,
            created_at=datetime.now(UTC),
        )
        with Session(self.engine) as db:
            db.add(
                ContextSummaryRow(
                    id=str(summary.id),
                    session_id=str(session_id),
                    content=summary.content,
                    source_message_ids_json=json.dumps(
                        [str(value) for value in source_message_ids]
                    ),
                    compacted_message_ids_json=json.dumps(
                        [str(value) for value in compacted_message_ids]
                    ),
                    before_tokens=before_tokens,
                    after_tokens=after_tokens,
                    created_at=summary.created_at,
                )
            )
            db.commit()
        return summary

    def latest_context_summary(
        self, session_id: UUID
    ) -> StoredContextSummary | None:
        with Session(self.engine) as db:
            row = db.scalar(
                select(ContextSummaryRow)
                .where(ContextSummaryRow.session_id == str(session_id))
                .order_by(ContextSummaryRow.created_at.desc())
                .limit(1)
            )
        if row is None:
            return None
        return StoredContextSummary(
            id=UUID(row.id),
            session_id=UUID(row.session_id),
            content=row.content,
            source_message_ids=[
                UUID(value) for value in json.loads(row.source_message_ids_json)
            ],
            compacted_message_ids=[
                UUID(value) for value in json.loads(row.compacted_message_ids_json)
            ],
            before_tokens=row.before_tokens,
            after_tokens=row.after_tokens,
            created_at=row.created_at,
        )

    def token_correction_coefficient(self, provider: str, model: str) -> float:
        key = f"{provider}:{model}:default"
        with Session(self.engine) as db:
            row = db.get(TokenCalibrationRow, key)
            return row.safe_coefficient if row else 1.0

    def update_token_calibration(
        self,
        provider: str,
        model: str,
        raw_estimate: int,
        actual_prompt: int,
    ) -> float:
        if raw_estimate <= 0 or actual_prompt <= 0:
            return self.token_correction_coefficient(provider, model)
        key = f"{provider}:{model}:default"
        ratio = min(max(actual_prompt / raw_estimate, 0.5), 2.0)
        with Session(self.engine) as db:
            row = db.get(TokenCalibrationRow, key)
            if row is None:
                row = TokenCalibrationRow(
                    profile_key=key,
                    provider=provider,
                    model=model,
                    updated_at=datetime.now(UTC),
                )
                db.add(row)
            ratios = [float(value) for value in json.loads(row.ratios_json or "[]")]
            ratios = [*ratios, ratio][-50:]
            alpha = 0.15
            row.log_coefficient = (
                (1 - alpha) * (row.log_coefficient or 0.0)
                + alpha * math.log(ratio)
            )
            ewma = math.exp(row.log_coefficient)
            ordered = sorted(ratios)
            p90_index = max(0, math.ceil(len(ordered) * 0.9) - 1)
            p90 = ordered[p90_index]
            row.safe_coefficient = min(max(ewma, p90, 1.0), 2.0)
            row.sample_count = (row.sample_count or 0) + 1
            row.ratios_json = json.dumps(ratios)
            row.last_raw_estimate = raw_estimate
            row.last_actual_prompt = actual_prompt
            row.updated_at = datetime.now(UTC)
            db.commit()
            return row.safe_coefficient

    def save_tool_artifact(
        self,
        source_ref: str,
        session_id: UUID,
        turn_id: UUID,
        tool_name: str,
        content: str,
        call_id: str | None = None,
        server_id: str | None = None,
        snapshot_id: str | None = None,
        schema_hash: str | None = None,
        requested_url: str | None = None,
        final_url: str | None = None,
        source_url: str | None = None,
        content_sha256: str | None = None,
        source_content_sha256: str | None = None,
        truncation_summary: dict[str, object] | None = None,
        parent_run_id: str | None = None,
        child_task_id: str | None = None,
        child_run_id: str | None = None,
        policy_decision_id: str | None = None,
        approval_id: str | None = None,
        access_level: str = "restricted",
        principal: str | None = None,
        expires_at: datetime | None = None,
    ) -> None:
        from starter_agent.agent.tool_result_guard import (
            redact_tool_result_content,
        )

        # Result envelopes are server-built, schema-bound and stored as restricted
        # artifacts; preserve canonical JSON so recovery can validate/hash it.
        safe_content = content if tool_name == "result_envelope" else redact_tool_result_content(content)
        safe_urls = json.loads(
            redact_tool_result_content(
                json.dumps(
                    {
                        "requested_url": requested_url,
                        "final_url": final_url,
                        "source_url": source_url,
                    },
                    ensure_ascii=False,
                )
            )
        )
        safe_summary = json.loads(
            redact_tool_result_content(
                json.dumps(truncation_summary or {}, ensure_ascii=False)
            )
        )
        with Session(self.engine) as db:
            db.merge(
                ToolArtifactRow(
                    source_ref=source_ref,
                    session_id=str(session_id),
                    turn_id=str(turn_id),
                    tool_name=tool_name,
                    call_id=call_id,
                    content=safe_content,
                    server_id=server_id,
                    snapshot_id=snapshot_id,
                    schema_hash=schema_hash,
                    requested_url=safe_urls.get("requested_url"),
                    final_url=safe_urls.get("final_url"),
                    source_url=safe_urls.get("source_url"),
                    content_sha256=content_sha256,
                    source_content_sha256=source_content_sha256,
                    truncation_summary_json=json.dumps(
                        safe_summary, ensure_ascii=False, separators=(",", ":")
                    ),
                    restricted=1,
                    parent_run_id=parent_run_id,
                    child_task_id=child_task_id,
                    child_run_id=child_run_id,
                    policy_decision_id=policy_decision_id,
                    approval_id=approval_id,
                    access_level=access_level,
                    principal=principal,
                    expires_at=expires_at,
                    created_at=datetime.now(UTC),
                )
            )
            db.commit()

    def _tool_artifact_payload(self, row: ToolArtifactRow, *, include_content: bool) -> dict[str, object]:
        payload = {
            "source_ref": row.source_ref,
            "session_id": UUID(row.session_id),
            "turn_id": UUID(row.turn_id),
            "tool_name": row.tool_name,
            "call_id": row.call_id,
            "server_id": row.server_id,
            "snapshot_id": row.snapshot_id,
            "schema_hash": row.schema_hash,
            "requested_url": row.requested_url,
            "final_url": row.final_url,
            "source_url": row.source_url,
            "content_sha256": row.content_sha256,
            "source_content_sha256": row.source_content_sha256,
            "truncation_summary": json.loads(row.truncation_summary_json or "{}"),
            "restricted": bool(row.restricted),
            "parent_run_id": row.parent_run_id,
            "child_task_id": row.child_task_id,
            "child_run_id": row.child_run_id,
            "policy_decision_id": row.policy_decision_id,
            "approval_id": row.approval_id,
            "access_level": row.access_level,
            "principal": row.principal,
            "expires_at": row.expires_at,
            "created_at": row.created_at,
        }
        if include_content:
            payload["content"] = row.content
        return payload

    @staticmethod
    def _artifact_expired(row: ToolArtifactRow, now: datetime) -> bool:
        expires_at = row.expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return expires_at is not None and expires_at <= now

    def get_tool_artifact(self, source_ref: str) -> dict[str, object] | None:
        """Internal legacy view; never disclose delegated restricted artifact bodies."""
        with Session(self.engine) as db:
            row = db.get(ToolArtifactRow, source_ref)
            if row is None:
                return None
            restricted_child = row.access_level == "child_restricted"
            expired = self._artifact_expired(row, datetime.now(UTC))
            payload = self._tool_artifact_payload(
                row, include_content=not restricted_child and not expired
            )
            if expired:
                payload["expired"] = True
            return payload

    def get_tool_artifact_for_principal(
        self, source_ref: str, *, principal: str, now: datetime | None = None
    ) -> dict[str, object] | None:
        """Authorized, on-demand view of restricted browser artifacts."""
        current = now or datetime.now(UTC)
        with Session(self.engine) as db:
            row = db.get(ToolArtifactRow, source_ref)
            if row is None or row.principal != principal:
                return None
            if self._artifact_expired(row, current):
                return self._tool_artifact_payload(row, include_content=False) | {"expired": True}
            return self._tool_artifact_payload(row, include_content=True)

    def purge_expired_tool_artifacts(self, *, now: datetime | None = None) -> int:
        """Irreversibly remove expired restricted bodies while retaining audit metadata."""
        current = now or datetime.now(UTC)
        with Session(self.engine) as db:
            rows = db.scalars(select(ToolArtifactRow).where(ToolArtifactRow.expires_at.is_not(None))).all()
            expired = [row for row in rows if self._artifact_expired(row, current) and row.content]
            for row in expired:
                row.content = ""
                row.requested_url = None
                row.final_url = None
                row.source_url = None
            db.commit()
            return len(expired)

    @staticmethod
    def _job_description_approval(row: JobDescriptionApprovalRow) -> dict[str, object]:
        return {
            "id": UUID(row.id),
            "principal": row.principal,
            "session_id": UUID(row.session_id),
            "turn_id": UUID(row.turn_id),
            "call_id": row.call_id,
            "artifact_ref": row.artifact_ref,
            "server_id": row.server_id,
            "snapshot_id": row.snapshot_id,
            "schema_hash": row.schema_hash,
            "source_url": row.source_url,
            "source_content_sha256": row.source_content_sha256,
            "artifact_content_sha256": row.artifact_content_sha256,
            "gate_reason_code": row.gate_reason_code,
            "status": row.status,
            "created_at": row.created_at,
            "approved_at": row.approved_at,
            "consumed_at": row.consumed_at,
        }

    def create_job_description_approval(
        self,
        *,
        principal: str,
        session_id: UUID,
        turn_id: UUID,
        call_id: str,
        artifact_ref: str,
        server_id: str,
        snapshot_id: str,
        schema_hash: str,
        source_url: str,
        source_content_sha256: str,
        artifact_content_sha256: str,
        gate_reason_code: str,
    ) -> dict[str, object]:
        row = JobDescriptionApprovalRow(
            id=str(uuid4()),
            principal=principal,
            session_id=str(session_id),
            turn_id=str(turn_id),
            call_id=call_id,
            artifact_ref=artifact_ref,
            server_id=server_id,
            snapshot_id=snapshot_id,
            schema_hash=schema_hash,
            source_url=source_url,
            source_content_sha256=source_content_sha256,
            artifact_content_sha256=artifact_content_sha256,
            gate_reason_code=gate_reason_code,
            status="pending",
            created_at=datetime.now(UTC),
        )
        with Session(self.engine) as db:
            db.add(row)
            db.commit()
            db.refresh(row)
            return self._job_description_approval(row)

    def get_job_description_approval(
        self, approval_id: UUID
    ) -> dict[str, object] | None:
        with Session(self.engine) as db:
            row = db.get(JobDescriptionApprovalRow, str(approval_id))
            return None if row is None else self._job_description_approval(row)

    def approve_job_description_ingestion(
        self,
        approval_id: UUID,
        *,
        principal: str,
        session_id: UUID,
    ) -> dict[str, object]:
        now = datetime.now(UTC)
        with Session(self.engine) as db, db.begin():
            row = db.get(JobDescriptionApprovalRow, str(approval_id))
            if row is None:
                raise ValueError("confirmation_not_found")
            if row.principal != principal or row.session_id != str(session_id):
                raise ValueError("confirmation_binding_mismatch")
            if row.status == "consumed":
                raise ValueError("confirmation_consumed")
            if row.status == "approved":
                return self._job_description_approval(row)
            result = db.execute(
                update(JobDescriptionApprovalRow)
                .where(
                    JobDescriptionApprovalRow.id == str(approval_id),
                    JobDescriptionApprovalRow.status == "pending",
                )
                .values(status="approved", approved_at=now)
            )
            if result.rowcount != 1:
                raise ValueError("confirmation_state_conflict")
            db.flush()
            refreshed = db.get(JobDescriptionApprovalRow, str(approval_id))
            assert refreshed is not None
            return self._job_description_approval(refreshed)

    def consume_job_description_approval(
        self,
        approval_id: UUID,
        *,
        principal: str,
        session_id: UUID,
    ) -> dict[str, object]:
        now = datetime.now(UTC)
        with Session(self.engine) as db, db.begin():
            row = db.get(JobDescriptionApprovalRow, str(approval_id))
            if row is None:
                raise ValueError("confirmation_not_found")
            if row.principal != principal or row.session_id != str(session_id):
                raise ValueError("confirmation_binding_mismatch")
            if row.status == "consumed":
                raise ValueError("confirmation_consumed")
            if row.status != "approved":
                raise ValueError("confirmation_not_approved")
            result = db.execute(
                update(JobDescriptionApprovalRow)
                .where(
                    JobDescriptionApprovalRow.id == str(approval_id),
                    JobDescriptionApprovalRow.status == "approved",
                )
                .values(status="consumed", consumed_at=now)
            )
            if result.rowcount != 1:
                raise ValueError("confirmation_consumed")
            db.flush()
            refreshed = db.get(JobDescriptionApprovalRow, str(approval_id))
            assert refreshed is not None
            return self._job_description_approval(refreshed)

    def restore_job_description_approval(
        self,
        approval_id: UUID,
        *,
        principal: str,
        session_id: UUID,
    ) -> dict[str, object]:
        """Restore a consumed approval only after its reserved write rolled back."""

        with Session(self.engine) as db, db.begin():
            row = db.get(JobDescriptionApprovalRow, str(approval_id))
            if row is None:
                raise ValueError("confirmation_not_found")
            if row.principal != principal or row.session_id != str(session_id):
                raise ValueError("confirmation_binding_mismatch")
            if row.status == "approved":
                return self._job_description_approval(row)
            if row.status != "consumed":
                raise ValueError("confirmation_state_conflict")
            result = db.execute(
                update(JobDescriptionApprovalRow)
                .where(
                    JobDescriptionApprovalRow.id == str(approval_id),
                    JobDescriptionApprovalRow.status == "consumed",
                )
                .values(status="approved", consumed_at=None)
            )
            if result.rowcount != 1:
                raise ValueError("confirmation_state_conflict")
            db.flush()
            refreshed = db.get(JobDescriptionApprovalRow, str(approval_id))
            assert refreshed is not None
            return self._job_description_approval(refreshed)

    def session_exists(self, session_id: UUID) -> bool:
        with Session(self.engine) as db:
            return db.get(SessionRow, str(session_id)) is not None

    def list_sessions(
        self, limit: int = 50, offset: int = 0
    ) -> list[StoredSessionSummary]:
        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        with Session(self.engine) as db:
            session_rows = list(
                db.scalars(
                    select(SessionRow)
                    .order_by(SessionRow.updated_at.desc())
                    .offset(offset)
                    .limit(limit)
                )
            )
            summaries: list[StoredSessionSummary] = []
            for session_row in session_rows:
                session_id = session_row.id
                message_count = db.scalar(
                    select(func.count())
                    .select_from(MessageRow)
                    .where(MessageRow.session_id == session_id)
                ) or 0
                first_user = db.scalar(
                    select(MessageRow.content)
                    .where(
                        MessageRow.session_id == session_id,
                        MessageRow.role == "user",
                        MessageRow.content != "",
                    )
                    .order_by(MessageRow.created_at.asc())
                    .limit(1)
                )
                last_message = db.scalar(
                    select(MessageRow.content)
                    .where(
                        MessageRow.session_id == session_id,
                        MessageRow.content != "",
                    )
                    .order_by(MessageRow.created_at.desc())
                    .limit(1)
                )
                summaries.append(
                    StoredSessionSummary(
                        id=UUID(session_id),
                        created_at=session_row.created_at,
                        updated_at=session_row.updated_at,
                        message_count=message_count,
                        first_user_message=first_user,
                        last_message=last_message,
                    )
                )
        return summaries

    def count_sessions(self) -> int:
        with Session(self.engine) as db:
            return int(db.scalar(select(func.count()).select_from(SessionRow)) or 0)

    def list_history_messages(
        self, session_id: UUID, limit: int = 100
    ) -> list[StoredHistoryMessage]:
        limit = max(1, min(limit, 500))
        with Session(self.engine) as db:
            rows = list(
                db.scalars(
                    select(MessageRow)
                    .where(MessageRow.session_id == str(session_id))
                    .order_by(MessageRow.created_at.desc())
                    .limit(limit)
                )
            )
        rows.reverse()
        return [
            StoredHistoryMessage(
                role=row.role,  # type: ignore[arg-type]
                content=row.content,
                name=row.name,
                tool_call_id=row.tool_call_id,
                created_at=row.created_at,
                turn_id=UUID(row.turn_id),
            )
            for row in rows
        ]

    def delete_session(self, session_id: UUID) -> bool:
        with Session(self.engine) as db:
            session = db.get(SessionRow, str(session_id))
            if session is None:
                return False
            db.execute(delete(MessageRow).where(MessageRow.session_id == str(session_id)))
            db.execute(delete(TurnUsageRow).where(TurnUsageRow.session_id == str(session_id)))
            db.execute(delete(ContextSummaryRow).where(ContextSummaryRow.session_id == str(session_id)))
            db.execute(delete(ToolArtifactRow).where(ToolArtifactRow.session_id == str(session_id)))
            db.delete(session)
            db.commit()
            return True

    def delete_all_sessions(self) -> int:
        """Delete conversation-scoped data while preserving long-term memory."""
        with Session(self.engine) as db:
            total = int(db.scalar(select(func.count()).select_from(SessionRow)) or 0)
            db.execute(delete(MessageRow))
            db.execute(delete(TurnUsageRow))
            db.execute(delete(ContextSummaryRow))
            db.execute(delete(ToolArtifactRow))
            db.execute(delete(SessionRow))
            db.commit()
            return total
