from __future__ import annotations

from typing import Any


_ALLOWED = {
    "operation",
    "document_id",
    "job_id",
    "fingerprint_prefix",
    "stage",
    "chunk_count",
    "duration_ms",
    "error_code",
}


def safe_knowledge_audit_fields(**values: Any) -> dict[str, Any]:
    return {
        key: value
        for key, value in values.items()
        if key in _ALLOWED and value is not None
    }
