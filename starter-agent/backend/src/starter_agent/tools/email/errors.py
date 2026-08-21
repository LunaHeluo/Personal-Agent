from __future__ import annotations

from enum import StrEnum
from typing import Any


class EmailErrorCode(StrEnum):
    INVALID_ARGUMENTS = "email_invalid_arguments"
    PROFILE_NOT_FOUND = "email_profile_not_found"
    PROFILE_DISABLED = "email_profile_disabled"
    CAPABILITY_NOT_SUPPORTED = "email_capability_not_supported"
    MISSING_CREDENTIALS = "email_missing_credentials"
    AUTHENTICATION_FAILED = "email_authentication_failed"
    INSUFFICIENT_SCOPE = "email_insufficient_scope"
    MAILBOX_UNAVAILABLE = "email_mailbox_unavailable"
    QUERY_INVALID = "email_query_invalid"
    CURSOR_INVALID = "email_cursor_invalid"
    MESSAGE_NOT_FOUND = "email_message_not_found"
    PARSE_FAILED = "email_parse_failed"
    DRAFT_NOT_FOUND = "email_draft_not_found"
    INVALID_RECIPIENT = "email_invalid_recipient"
    PLACEHOLDER_PRESENT = "email_placeholder_present"
    ATTACHMENT_NOT_FOUND = "email_attachment_not_found"
    ATTACHMENT_CHANGED = "email_attachment_changed"
    DRAFT_CHANGED = "email_draft_changed"
    APPROVAL_REQUIRED = "email_approval_required"
    APPROVAL_EXPIRED = "email_approval_expired"
    APPROVAL_INVALID = "email_approval_invalid"
    APPROVAL_CONSUMED = "email_approval_consumed"
    REAL_SEND_DISABLED = "email_real_send_disabled"
    RATE_LIMITED = "email_rate_limited"
    PROVIDER_TIMEOUT = "email_provider_timeout"
    TRANSPORT_ERROR = "email_transport_error"
    SEND_REJECTED = "email_send_rejected"
    SEND_STATUS_UNKNOWN = "email_send_status_unknown"
    IDEMPOTENCY_CONFLICT = "email_idempotency_conflict"
    FIXTURE_INVALID = "email_fixture_invalid"
    INTERNAL_ERROR = "email_internal_error"


class EmailError(Exception):
    """A provider-neutral, safe-to-return email failure."""

    def __init__(
        self,
        code: EmailErrorCode,
        display: str,
        *,
        retryable: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(display)
        self.code = code
        self.display = display
        self.retryable = retryable
        self.metadata = dict(metadata or {})

    def public_payload(self) -> dict[str, Any]:
        return {
            "error_code": self.code.value,
            "display": self.display,
            "retryable": self.retryable,
            "metadata": self.metadata,
        }
