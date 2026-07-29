from __future__ import annotations

import re
from typing import Any

from starter_agent.mcp.config import contains_high_confidence_secret


REDACTED = "<redacted>"

_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|authorization|cookie|credential|pass(?:word|wd)?|secret|token)",
    flags=re.IGNORECASE,
)
_SECRET_TEXT = re.compile(
    r"(?i)(bearer\s+\S+|(?:api[_-]?key|authorization|cookie|password|secret|token)\s*[=:]\s*\S+)"
)


def redact_trust_payload(value: Any, *, sensitive: bool = False) -> Any:
    if isinstance(value, dict):
        return {
            str(key): redact_trust_payload(
                item,
                sensitive=sensitive or bool(_SENSITIVE_KEY.search(str(key))),
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_trust_payload(item, sensitive=sensitive) for item in value]
    if isinstance(value, str):
        if sensitive:
            return REDACTED
        redacted = _SECRET_TEXT.sub(REDACTED, value)
        if contains_high_confidence_secret(redacted):
            return REDACTED
        return redacted
    return value
