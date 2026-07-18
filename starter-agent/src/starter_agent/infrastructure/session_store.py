from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, create_engine, delete, func, inspect, select, text
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
    content: Mapped[str] = mapped_column(Text)
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

    def create_session(self) -> UUID:
        session_id = uuid4()
        now = datetime.now(UTC)
        with Session(self.engine) as db:
            db.add(SessionRow(id=str(session_id), created_at=now, updated_at=now))
            db.commit()
        return session_id

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

    def add_message(self, session_id: UUID, turn_id: UUID, message: Message) -> UUID:
        now = datetime.now(UTC)
        message_id = uuid4()
        with Session(self.engine) as db:
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
    ) -> None:
        with Session(self.engine) as db:
            db.merge(
                ToolArtifactRow(
                    source_ref=source_ref,
                    session_id=str(session_id),
                    turn_id=str(turn_id),
                    tool_name=tool_name,
                    content=content,
                    created_at=datetime.now(UTC),
                )
            )
            db.commit()

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
