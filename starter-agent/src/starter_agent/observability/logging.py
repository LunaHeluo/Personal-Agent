from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Any

import structlog


_SENSITIVE_KEY = re.compile(
    r"password|credential|authorization|auth_code|api[_-]?key|"
    r"access[_-]?token|refresh[_-]?token|body_text|snippet|"
    r"subject|sender|recipient|attachment_name|query_text",
    re.IGNORECASE,
)
_SECRET_OR_ADDRESS = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b|"
    r"\bsk-[A-Z0-9_-]{8,}\b|"
    r"(?:password|credential|authorization|token)\s*[:=]\s*\S+",
    re.IGNORECASE,
)


def redact_sensitive_log_fields(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    for key, value in list(event_dict.items()):
        if _SENSITIVE_KEY.search(str(key)):
            event_dict[key] = "[REDACTED]"
            continue
        if isinstance(value, str) and _SECRET_OR_ADDRESS.search(value):
            event_dict[key] = "[REDACTED]"
    return event_dict


def configure_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        format="%(message)s",
        level=logging.INFO,
        handlers=[
            logging.StreamHandler(sys.stderr),
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
        force=True,
    )
    # httpx logs the full request URL at INFO. SerpAPI authenticates through a
    # query parameter, so those records could expose credentials.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("imaplib").setLevel(logging.WARNING)
    logging.getLogger("smtplib").setLevel(logging.WARNING)
    logging.getLogger("mailbox").setLevel(logging.WARNING)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_sensitive_log_fields,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(**context: Any) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger().bind(**context)
