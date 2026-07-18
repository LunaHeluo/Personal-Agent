from __future__ import annotations

import hashlib
import hmac
import json
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import DateTime, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from starter_agent.tools.email.errors import EmailError, EmailErrorCode
from starter_agent.tools.email.models import SendApproval, SendReceipt, StoredDraft


class EmailBase(DeclarativeBase):
    pass


class EmailReferenceRow(EmailBase):
    __tablename__ = "email_references"

    reference: Mapped[str] = mapped_column(String(160), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    profile: Mapped[str] = mapped_column(String(80), index=True)
    object_type: Mapped[str] = mapped_column(String(40), index=True)
    object_id: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EmailDraftRow(EmailBase):
    __tablename__ = "email_drafts"

    draft_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    profile: Mapped[str] = mapped_column(String(80), index=True)
    content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EmailApprovalRow(EmailBase):
    __tablename__ = "email_send_approvals"

    approval_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    profile: Mapped[str] = mapped_column(String(80), index=True)
    draft_id: Mapped[str] = mapped_column(String(160), index=True)
    content_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(40), index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EmailIdempotencyRow(EmailBase):
    __tablename__ = "email_idempotency"

    operation_key: Mapped[str] = mapped_column(String(180), primary_key=True)
    operation: Mapped[str] = mapped_column(String(40), index=True)
    key_hash: Mapped[str] = mapped_column(String(64), index=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    result_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EmailReceiptRow(EmailBase):
    __tablename__ = "email_send_receipts"

    receipt_key: Mapped[str] = mapped_column(String(180), primary_key=True)
    draft_id: Mapped[str] = mapped_column(String(160), index=True)
    key_hash: Mapped[str] = mapped_column(String(64), index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def stable_sha256(value: Any) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def idempotency_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def draft_content_sha256(
    *,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    subject: str,
    body_text: str,
    in_reply_to: str | None,
    attachment_sha256s: list[str],
) -> str:
    return stable_sha256(
        {
            "to": sorted(address.strip().lower() for address in to),
            "cc": sorted(address.strip().lower() for address in cc),
            "bcc": sorted(address.strip().lower() for address in bcc),
            "subject": subject.replace("\r\n", "\n").strip(),
            "body_text": body_text.replace("\r\n", "\n").strip(),
            "in_reply_to": in_reply_to,
            "attachment_sha256s": attachment_sha256s,
        }
    )


def recipient_sha256(to: list[str], cc: list[str], bcc: list[str]) -> str:
    return stable_sha256(
        sorted(address.strip().lower() for address in [*to, *cc, *bcc])
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class EmailStore(ABC):
    @abstractmethod
    def create_reference(
        self,
        *,
        session_id: str,
        profile: str,
        object_type: str,
        object_id: str,
        expires_at: datetime | None = None,
    ) -> str: ...

    @abstractmethod
    def resolve_reference(
        self,
        reference: str,
        *,
        session_id: str,
        profile: str,
        object_type: str,
    ) -> str: ...

    @abstractmethod
    def save_draft(self, draft: StoredDraft) -> StoredDraft: ...

    @abstractmethod
    def get_draft(
        self, draft_id: str, *, session_id: str, profile: str | None = None
    ) -> StoredDraft: ...

    @abstractmethod
    def save_approval(self, approval: SendApproval) -> SendApproval: ...

    @abstractmethod
    def get_approval(
        self, approval_id: str, *, session_id: str
    ) -> SendApproval: ...

    @abstractmethod
    def save_receipt(
        self,
        draft_id: str,
        idempotency_key: str,
        receipt: SendReceipt,
    ) -> SendReceipt: ...

    @abstractmethod
    def find_receipt(
        self, draft_id: str, idempotency_key: str
    ) -> SendReceipt | None: ...

    @abstractmethod
    def get_idempotency(
        self,
        operation: str,
        key: str,
        request_hash: str,
    ) -> dict[str, Any] | None: ...

    @abstractmethod
    def save_idempotency(
        self,
        operation: str,
        key: str,
        request_hash: str,
        result: dict[str, Any],
    ) -> None: ...

    @abstractmethod
    def update_approval_status(
        self,
        approval_id: str,
        *,
        session_id: str,
        status: str,
    ) -> SendApproval: ...

    @abstractmethod
    def invalidate_draft_approvals(self, draft_id: str) -> None: ...


class SQLiteEmailStore(EmailStore):
    def __init__(self, database_url: str, project_root: Path):
        if database_url.startswith("sqlite:///"):
            relative = database_url.removeprefix("sqlite:///")
            db_path = Path(relative)
            if not db_path.is_absolute():
                db_path = project_root / db_path
            db_path.parent.mkdir(parents=True, exist_ok=True)
            database_url = f"sqlite:///{db_path}"
        self.engine = create_engine(database_url)
        EmailBase.metadata.create_all(self.engine)

    def create_reference(
        self,
        *,
        session_id: str,
        profile: str,
        object_type: str,
        object_id: str,
        expires_at: datetime | None = None,
    ) -> str:
        reference = f"email-{object_type}:{uuid4()}"
        row = EmailReferenceRow(
            reference=reference,
            session_id=session_id,
            profile=profile,
            object_type=object_type,
            object_id=object_id,
            expires_at=expires_at,
            created_at=datetime.now(UTC),
        )
        with Session(self.engine) as db:
            db.add(row)
            db.commit()
        return reference

    def resolve_reference(
        self,
        reference: str,
        *,
        session_id: str,
        profile: str,
        object_type: str,
    ) -> str:
        with Session(self.engine) as db:
            row = db.get(EmailReferenceRow, reference)
            if (
                row is None
                or not hmac.compare_digest(row.session_id, session_id)
                or not hmac.compare_digest(row.profile, profile)
                or row.object_type != object_type
            ):
                raise EmailError(
                    EmailErrorCode.MESSAGE_NOT_FOUND,
                    "邮件引用不存在或不属于当前会话",
                )
            if (
                row.expires_at is not None
                and _aware(row.expires_at) <= datetime.now(UTC)
            ):
                raise EmailError(
                    EmailErrorCode.MESSAGE_NOT_FOUND,
                    "邮件引用已过期",
                )
            return row.object_id

    def save_draft(self, draft: StoredDraft) -> StoredDraft:
        payload = draft.model_dump_json()
        with Session(self.engine) as db:
            row = db.get(EmailDraftRow, draft.draft_id)
            if row is None:
                row = EmailDraftRow(
                    draft_id=draft.draft_id,
                    session_id=draft.session_id,
                    profile=draft.profile,
                    content_sha256=draft.content_sha256,
                    status=draft.status,
                    idempotency_key_hash=draft.idempotency_key_hash,
                    payload_json=payload,
                    created_at=draft.created_at,
                    updated_at=draft.updated_at,
                )
                db.add(row)
            else:
                if row.session_id != draft.session_id or row.profile != draft.profile:
                    raise EmailError(
                        EmailErrorCode.DRAFT_NOT_FOUND,
                        "草稿不存在或不属于当前会话",
                    )
                if row.content_sha256 != draft.content_sha256:
                    self._invalidate_approvals(db, draft.draft_id)
                row.content_sha256 = draft.content_sha256
                row.status = draft.status
                row.idempotency_key_hash = draft.idempotency_key_hash
                row.payload_json = payload
                row.updated_at = draft.updated_at
            db.commit()
        return draft

    def get_draft(
        self, draft_id: str, *, session_id: str, profile: str | None = None
    ) -> StoredDraft:
        with Session(self.engine) as db:
            row = db.get(EmailDraftRow, draft_id)
            if (
                row is None
                or not hmac.compare_digest(row.session_id, session_id)
                or (profile is not None and row.profile != profile)
            ):
                raise EmailError(
                    EmailErrorCode.DRAFT_NOT_FOUND,
                    "草稿不存在或不属于当前会话",
                )
            return StoredDraft.model_validate_json(row.payload_json)

    def save_approval(self, approval: SendApproval) -> SendApproval:
        now = datetime.now(UTC)
        with Session(self.engine) as db:
            row = db.get(EmailApprovalRow, approval.approval_id)
            payload = approval.model_dump_json()
            if row is None:
                row = EmailApprovalRow(
                    approval_id=approval.approval_id,
                    session_id=approval.session_id,
                    profile=approval.profile,
                    draft_id=approval.draft_id,
                    content_sha256=approval.content_sha256,
                    status=approval.status,
                    payload_json=payload,
                    expires_at=approval.expires_at,
                    updated_at=now,
                )
                db.add(row)
            else:
                if row.session_id != approval.session_id:
                    raise EmailError(
                        EmailErrorCode.APPROVAL_INVALID,
                        "审批不属于当前会话",
                    )
                row.status = approval.status
                row.payload_json = payload
                row.updated_at = now
            db.commit()
        return approval

    def get_approval(
        self, approval_id: str, *, session_id: str
    ) -> SendApproval:
        with Session(self.engine) as db:
            row = db.get(EmailApprovalRow, approval_id)
            if row is None or not hmac.compare_digest(row.session_id, session_id):
                raise EmailError(
                    EmailErrorCode.APPROVAL_INVALID,
                    "发送审批不存在或不属于当前会话",
                )
            approval = SendApproval.model_validate_json(row.payload_json)
            if (
                approval.status in {"pending", "approved"}
                and _aware(approval.expires_at) <= datetime.now(UTC)
            ):
                approval.status = "expired"
                row.status = approval.status
                row.payload_json = approval.model_dump_json()
                row.updated_at = datetime.now(UTC)
                db.commit()
            return approval

    def update_approval_status(
        self,
        approval_id: str,
        *,
        session_id: str,
        status: str,
    ) -> SendApproval:
        approval = self.get_approval(approval_id, session_id=session_id)
        if status not in {
            "pending",
            "approved",
            "consumed",
            "expired",
            "invalidated",
        }:
            raise ValueError("Unsupported approval status")
        approval.status = status  # type: ignore[assignment]
        if status == "approved":
            approval.approved_at = datetime.now(UTC)
        return self.save_approval(approval)

    def invalidate_draft_approvals(self, draft_id: str) -> None:
        with Session(self.engine) as db:
            self._invalidate_approvals(db, draft_id)
            db.commit()

    @staticmethod
    def _invalidate_approvals(db: Session, draft_id: str) -> None:
        rows = db.scalars(
            select(EmailApprovalRow).where(
                EmailApprovalRow.draft_id == draft_id,
                EmailApprovalRow.status.in_(["pending", "approved"]),
            )
        ).all()
        for row in rows:
            approval = SendApproval.model_validate_json(row.payload_json)
            approval.status = "invalidated"
            row.status = approval.status
            row.payload_json = approval.model_dump_json()
            row.updated_at = datetime.now(UTC)

    def get_idempotency(
        self,
        operation: str,
        key: str,
        request_hash: str,
    ) -> dict[str, Any] | None:
        key_hash = idempotency_hash(key)
        operation_key = f"{operation}:{key_hash}"
        with Session(self.engine) as db:
            row = db.get(EmailIdempotencyRow, operation_key)
            if row is None:
                return None
            if not hmac.compare_digest(row.request_hash, request_hash):
                raise EmailError(
                    EmailErrorCode.IDEMPOTENCY_CONFLICT,
                    "相同幂等键对应了不同请求",
                )
            return json.loads(row.result_json)

    def save_idempotency(
        self,
        operation: str,
        key: str,
        request_hash: str,
        result: dict[str, Any],
    ) -> None:
        key_hash = idempotency_hash(key)
        operation_key = f"{operation}:{key_hash}"
        with Session(self.engine) as db:
            existing = db.get(EmailIdempotencyRow, operation_key)
            if existing is not None:
                if not hmac.compare_digest(existing.request_hash, request_hash):
                    raise EmailError(
                        EmailErrorCode.IDEMPOTENCY_CONFLICT,
                        "相同幂等键对应了不同请求",
                    )
                return
            db.add(
                EmailIdempotencyRow(
                    operation_key=operation_key,
                    operation=operation,
                    key_hash=key_hash,
                    request_hash=request_hash,
                    result_json=json.dumps(
                        result, ensure_ascii=False, separators=(",", ":")
                    ),
                    created_at=datetime.now(UTC),
                )
            )
            db.commit()

    def save_receipt(
        self,
        draft_id: str,
        idempotency_key: str,
        receipt: SendReceipt,
    ) -> SendReceipt:
        key_hash = idempotency_hash(idempotency_key)
        receipt_key = f"{draft_id}:{key_hash}"
        with Session(self.engine) as db:
            row = db.get(EmailReceiptRow, receipt_key)
            if row is None:
                db.add(
                    EmailReceiptRow(
                        receipt_key=receipt_key,
                        draft_id=draft_id,
                        key_hash=key_hash,
                        payload_json=receipt.model_dump_json(),
                        created_at=datetime.now(UTC),
                    )
                )
                db.commit()
                return receipt
            return SendReceipt.model_validate_json(row.payload_json)

    def find_receipt(
        self, draft_id: str, idempotency_key: str
    ) -> SendReceipt | None:
        key_hash = idempotency_hash(idempotency_key)
        with Session(self.engine) as db:
            row = db.get(EmailReceiptRow, f"{draft_id}:{key_hash}")
            if row is None:
                return None
            return SendReceipt.model_validate_json(row.payload_json)
