from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


EmailDraftStatus = Literal[
    "draft_only",
    "waiting_for_approval",
    "approved",
    "sending",
    "sent",
    "simulated_sent",
    "send_failed",
    "send_status_unknown",
    "invalidated",
]
ApprovalStatus = Literal[
    "pending",
    "approved",
    "consumed",
    "expired",
    "invalidated",
]


class EmailCapabilities(BaseModel):
    search: bool = False
    read_peek: bool = False
    local_draft: bool = True
    mailbox_draft: bool = False
    simulated_send: bool = False
    real_send: bool = False


class ResultCompleteness(BaseModel):
    is_truncated: bool = False
    has_more: bool = False
    source_ref: str


class EmailSearchQuery(BaseModel):
    sender: str | None = Field(default=None, max_length=320)
    recipient: str | None = Field(default=None, max_length=320)
    subject: str | None = Field(default=None, max_length=300)
    keywords: list[str] = Field(default_factory=list, max_length=10)
    date_from: datetime | None = None
    date_to: datetime | None = None
    unread_only: bool = False
    mailbox: str = Field(default="INBOX", min_length=1, max_length=120)
    limit: int = Field(default=10, ge=1, le=50)
    cursor: str | None = Field(default=None, max_length=500)


class EmailMessageSummary(BaseModel):
    message_ref: str
    thread_ref: str | None = None
    from_masked: str
    to_me: bool = True
    subject: str
    sent_at: datetime
    snippet: str = ""
    flags: list[str] = Field(default_factory=list)
    source_ref: str


class EmailSearchPage(BaseModel):
    profile: str
    messages: list[EmailMessageSummary] = Field(default_factory=list)
    next_cursor: str | None = None
    is_truncated: bool = False
    has_more: bool = False
    source_ref: str


class EmailHeaders(BaseModel):
    from_masked: str
    to_masked: list[str] = Field(default_factory=list)
    cc_masked: list[str] = Field(default_factory=list)
    subject: str
    date: datetime
    reply_to_masked: str | None = None


class EmailAttachmentMeta(BaseModel):
    attachment_ref: str
    filename_masked: str
    content_type: str
    size_bytes: int = Field(ge=0)
    sha256: str | None = None


class EmailReplyContext(BaseModel):
    in_reply_to: str | None = None
    references_count: int = Field(default=0, ge=0)
    thread_position: int = Field(default=1, ge=1)


class EmailMessage(BaseModel):
    message_ref: str
    thread_ref: str | None = None
    headers: EmailHeaders
    body_text: str
    attachments: list[EmailAttachmentMeta] = Field(default_factory=list)
    reply_context: EmailReplyContext = Field(default_factory=EmailReplyContext)
    thread_messages: list[EmailMessageSummary] = Field(default_factory=list)
    is_truncated: bool = False
    has_more: bool = False
    source_ref: str


class DraftCreateRequest(BaseModel):
    profile: str
    storage_scope: Literal["local", "mailbox", "mock"]
    to: list[str] = Field(min_length=1, max_length=10)
    cc: list[str] = Field(default_factory=list, max_length=10)
    bcc: list[str] = Field(default_factory=list, max_length=10)
    subject: str = Field(min_length=1, max_length=300)
    body_text: str = Field(min_length=1, max_length=50_000)
    in_reply_to: str | None = Field(default=None, max_length=500)
    attachment_refs: list[str] = Field(default_factory=list, max_length=10)
    evidence_source_refs: list[str] = Field(default_factory=list, max_length=30)
    idempotency_key: str = Field(min_length=16, max_length=200)


class StoredAttachment(BaseModel):
    attachment_ref: str
    path: str | None = None
    size_bytes: int = Field(ge=0)
    sha256: str


class StoredDraft(BaseModel):
    draft_id: str
    provider_draft_id: str | None = None
    session_id: str
    profile: str
    storage_scope: Literal["local", "mailbox", "mock"]
    to: list[str]
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    subject: str
    body_text: str
    in_reply_to: str | None = None
    attachments: list[StoredAttachment] = Field(default_factory=list)
    content_sha256: str
    status: EmailDraftStatus = "draft_only"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    idempotency_key_hash: str


class SendApproval(BaseModel):
    approval_id: str
    session_id: str
    user_ref: str | None = None
    profile: str
    draft_id: str
    content_sha256: str
    recipient_sha256: str
    attachment_sha256s: list[str] = Field(default_factory=list)
    status: ApprovalStatus = "pending"
    approved_at: datetime | None = None
    expires_at: datetime


class ApprovalChallengeView(BaseModel):
    approval_id: str
    session_id: str
    profile: str
    draft_id: str
    status: ApprovalStatus
    to: list[str]
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    subject: str
    body_text: str
    attachment_sha256s: list[str] = Field(default_factory=list)
    content_sha256: str
    expires_at: datetime


class SendReceipt(BaseModel):
    delivery_mode: Literal["mock", "real"]
    status: Literal["simulated_sent", "sent", "rejected", "unknown"]
    external_delivery: bool
    message_ref: str | None = None
    thread_ref: str | None = None
    sent_at: datetime | None = None
    content_sha256: str
    recipient_count: int = Field(ge=0)
    idempotency_key_hash: str
    source_ref: str
