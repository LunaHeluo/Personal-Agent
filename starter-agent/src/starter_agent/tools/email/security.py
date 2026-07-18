from __future__ import annotations

from typing import Any


EMAIL_AUDIT_FIELDS = {
    "operation",
    "profile",
    "adapter",
    "ok",
    "error_code",
    "retryable",
    "result_count",
    "is_truncated",
    "has_more",
    "draft_id_hash",
    "content_sha256",
    "recipient_count",
    "attachment_count",
    "delivery_mode",
    "external_delivery",
    "duration_ms",
}


def safe_email_audit_fields(**values: Any) -> dict[str, Any]:
    """Return only non-content fields approved for email audit logs."""

    return {
        key: value
        for key, value in values.items()
        if key in EMAIL_AUDIT_FIELDS and value is not None
    }
