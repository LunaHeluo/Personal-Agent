from pathlib import Path

import pytest
from pydantic import ValidationError

from starter_agent.settings import (
    AgentSettings,
    EmailProfileConfig,
    EmailToolConfig,
    ProviderConfig,
)
from starter_agent.tools.email.errors import EmailError, EmailErrorCode


def agent_settings(tmp_path: Path, email: EmailToolConfig) -> AgentSettings:
    return AgentSettings(
        providers={"mock": ProviderConfig(type="mock")},
        project_root=tmp_path,
        tools={"email": email},
    )


def test_mock_email_profile_is_valid_and_real_send_defaults_off(
    tmp_path: Path,
) -> None:
    config = EmailToolConfig(
        active_profile="mock",
        profiles={
            "mock": EmailProfileConfig(
                adapter="mock_fixture",
                fixture_root="tests/fixtures/email",
            )
        },
    )

    settings = agent_settings(tmp_path, config)

    assert settings.tools.email.active_profile == "mock"
    assert settings.tools.email.profiles["mock"].real_send_enabled is False


def test_email_profile_rejects_invalid_port() -> None:
    with pytest.raises(ValidationError):
        EmailProfileConfig.model_validate(
            {
                "adapter": "imap_smtp",
                "mailbox_type": "custom",
                "account_env": "EMAIL_ACCOUNT",
                "auth": {
                    "type": "app_password",
                    "credential_env": "EMAIL_PASSWORD",
                },
                "imap": {
                    "host": "imap.example.test",
                    "port": 0,
                    "transport": "ssl_tls",
                },
                "smtp": {
                    "host": "smtp.example.test",
                    "port": 465,
                    "transport": "ssl_tls",
                },
            }
        )


def test_imap_smtp_profile_requires_explicit_connection_fields() -> None:
    with pytest.raises(ValidationError, match="missing"):
        EmailProfileConfig(
            adapter="imap_smtp",
            mailbox_type="custom",
            account_env="EMAIL_ACCOUNT",
        )


def test_mock_fixture_path_must_stay_inside_project_root(tmp_path: Path) -> None:
    config = EmailToolConfig(
        profiles={
            "mock": EmailProfileConfig(
                adapter="mock_fixture",
                fixture_root="../private-email",
            )
        }
    )

    with pytest.raises(ValidationError, match="outside project root"):
        agent_settings(tmp_path, config)


def test_oauth_profile_uses_environment_names_not_secret_values(
    tmp_path: Path,
) -> None:
    config = EmailToolConfig(
        active_profile="gmail",
        profiles={
            "gmail": EmailProfileConfig(
                adapter="imap_smtp",
                mailbox_type="gmail",
                account_env="GMAIL_ACCOUNT",
                auth={
                    "type": "oauth",
                    "oauth_client_id_env": "GMAIL_OAUTH_CLIENT_ID",
                    "oauth_refresh_token_env": "GMAIL_OAUTH_REFRESH_TOKEN",
                },
                imap={
                    "host": "imap.example.test",
                    "port": 993,
                    "transport": "ssl_tls",
                },
                smtp={
                    "host": "smtp.example.test",
                    "port": 465,
                    "transport": "ssl_tls",
                },
            )
        },
    )

    dumped = agent_settings(tmp_path, config).model_dump_json()

    assert "GMAIL_OAUTH_REFRESH_TOKEN" in dumped
    assert "secret-value" not in dumped
    assert config.profiles["gmail"].real_send_enabled is False


def test_email_error_public_payload_is_stable_and_safe() -> None:
    error = EmailError(
        EmailErrorCode.MISSING_CREDENTIALS,
        "邮箱凭据未配置",
        metadata={"profile": "personal", "credential_env": "EMAIL_PASSWORD"},
    )

    assert error.public_payload() == {
        "error_code": "email_missing_credentials",
        "display": "邮箱凭据未配置",
        "retryable": False,
        "metadata": {
            "profile": "personal",
            "credential_env": "EMAIL_PASSWORD",
        },
    }
