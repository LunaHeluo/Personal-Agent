import logging

from starter_agent.observability.logging import (
    configure_logging,
    get_logger,
    redact_sensitive_log_fields,
)
from starter_agent.tools.email.security import safe_email_audit_fields


def test_http_client_info_logs_are_disabled(tmp_path) -> None:
    configure_logging(tmp_path / "agent.jsonl")

    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("imaplib").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("smtplib").getEffectiveLevel() >= logging.WARNING


def test_sensitive_log_processor_redacts_email_content_and_secrets() -> None:
    payload = redact_sensitive_log_fields(
        None,
        "info",
        {
            "event": "email.completed",
            "subject": "Interview invitation",
            "body_text": "private body",
            "credential": "unique-secret",
            "message": "sent to candidate@example.test",
            "profile": "personal",
        },
    )

    assert payload["event"] == "email.completed"
    assert payload["profile"] == "personal"
    assert payload["subject"] == "[REDACTED]"
    assert payload["body_text"] == "[REDACTED]"
    assert payload["credential"] == "[REDACTED]"
    assert payload["message"] == "[REDACTED]"


def test_email_audit_uses_field_allowlist() -> None:
    fields = safe_email_audit_fields(
        operation="search",
        profile="mock",
        result_count=2,
        subject="must not be logged",
        body_text="must not be logged",
        recipient="candidate@example.test",
    )

    assert fields == {
        "operation": "search",
        "profile": "mock",
        "result_count": 2,
    }


def test_configured_logger_does_not_write_sensitive_values(tmp_path) -> None:
    path = tmp_path / "agent.jsonl"
    configure_logging(path)
    get_logger().info(
        "email.test",
        subject="UNIQUE_PRIVATE_SUBJECT",
        recipient="private-person@example.test",
        credential="UNIQUE_EMAIL_CREDENTIAL",
        profile="mock",
    )
    for handler in logging.getLogger().handlers:
        handler.flush()
    content = path.read_text(encoding="utf-8")

    assert "UNIQUE_PRIVATE_SUBJECT" not in content
    assert "private-person@example.test" not in content
    assert "UNIQUE_EMAIL_CREDENTIAL" not in content
    assert '"profile": "mock"' in content
