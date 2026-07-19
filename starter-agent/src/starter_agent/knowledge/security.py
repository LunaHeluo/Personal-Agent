from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from starter_agent.knowledge.errors import KnowledgeError


_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SENSITIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "api_key",
        re.compile(
            r"\bsk-[A-Za-z0-9_-]{12,}\b|"
            r"\bapi[_-]?key\s*[:=]\s*[A-Za-z0-9_-]{12,}",
            re.IGNORECASE,
        ),
    ),
    (
        "credential",
        re.compile(
            r"\b(?:password|passwd|authorization|auth[_-]?code|access[_-]?token)"
            r"\s*[:=]\s*\S+",
            re.IGNORECASE,
        ),
    ),
    (
        "identity_number",
        re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
    ),
)


@dataclass(frozen=True)
class ValidatedUpload:
    filename: str
    text: str
    content_sha256: str


def validate_markdown_upload(
    *,
    filename: str,
    content: bytes,
    confirmed_authorized: bool,
    max_bytes: int,
    allowed_extensions: list[str],
) -> ValidatedUpload:
    if not confirmed_authorized:
        raise KnowledgeError("upload_authorization_required")
    if not filename or Path(filename).name != filename or any(
        value in filename for value in ("/", "\\")
    ):
        raise KnowledgeError("unsupported_document_type")
    if Path(filename).suffix.lower() not in {
        value.lower() for value in allowed_extensions
    }:
        raise KnowledgeError("unsupported_document_type")
    if len(content) > max_bytes:
        raise KnowledgeError("document_too_large")
    if b"\x00" in content:
        raise KnowledgeError("document_invalid_encoding")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise KnowledgeError("document_invalid_encoding") from exc
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if _CONTROL_CHARACTERS.search(text):
        raise KnowledgeError("document_invalid_encoding")
    for rule_id, pattern in _SENSITIVE_PATTERNS:
        if pattern.search(text):
            raise KnowledgeError("sensitive_content_detected", rule_id=rule_id)
    return ValidatedUpload(
        filename=filename,
        text=text,
        content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )
