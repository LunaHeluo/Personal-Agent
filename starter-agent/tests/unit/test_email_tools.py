from pathlib import Path
from uuid import uuid4

from starter_agent.settings import AgentSettings, ProviderConfig, ToolsConfig
from starter_agent.tools.base import ToolContext
from starter_agent.tools.email.tools import (
    EmailCreateDraftTool,
    EmailReadTool,
    EmailSearchTool,
    EmailSendTool,
)
from starter_agent.tools.registry import ToolRegistry


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def settings(tmp_path) -> AgentSettings:
    return AgentSettings(
        providers={"mock": ProviderConfig(type="mock")},
        project_root=PROJECT_ROOT,
        app={"database_url": f"sqlite:///{tmp_path / 'tools-email.db'}"},
        tools=ToolsConfig.model_validate(
            {
                "enabled": [
                    "email_search",
                    "email_read",
                    "email_create_draft",
                    "email_send",
                ],
                "allow_risk_levels": ["read", "write"],
                "email": {
                    "active_profile": "mock",
                    "attachment_root": "tests/fixtures/email/attachments",
                    "profiles": {
                        "mock": {
                            "adapter": "mock_fixture",
                            "fixture_root": "tests/fixtures/email",
                        }
                    },
                },
            }
        ),
    )


def real_settings(tmp_path) -> AgentSettings:
    return AgentSettings(
        providers={"mock": ProviderConfig(type="mock")},
        project_root=PROJECT_ROOT,
        app={"database_url": f"sqlite:///{tmp_path / 'real-tools-email.db'}"},
        tools=ToolsConfig.model_validate(
            {
                "enabled": [
                    "email_create_draft",
                    "email_send",
                ],
                "allow_risk_levels": ["write", "external"],
                "email": {
                    "active_profile": "qq",
                    "profiles": {
                        "qq": {
                            "adapter": "imap_smtp",
                            "mailbox_type": "qq",
                            "account_env": "EMAIL_ACCOUNT",
                            "auth": {
                                "type": "qq_auth_code",
                                "credential_env": "EMAIL_CREDENTIAL",
                            },
                            "imap": {
                                "host": "imap.qq.com",
                                "port": 993,
                                "transport": "ssl_tls",
                            },
                            "smtp": {
                                "host": "smtp.qq.com",
                                "port": 465,
                                "transport": "ssl_tls",
                            },
                            "real_send_enabled": True,
                        }
                    },
                },
            }
        ),
    )


def context() -> ToolContext:
    return ToolContext(session_id=uuid4(), turn_id=uuid4())


async def test_email_tools_share_manager_and_execute_search_read_draft(
    tmp_path,
) -> None:
    registry = ToolRegistry(
        settings(tmp_path).tools.enabled, settings=settings(tmp_path)
    )
    search = registry.get("email_search")
    read = registry.get("email_read")
    create = registry.get("email_create_draft")

    assert isinstance(search, EmailSearchTool)
    assert isinstance(read, EmailReadTool)
    assert isinstance(create, EmailCreateDraftTool)
    assert search.manager is read.manager is create.manager
    tool_context = context()
    searched = await search.execute(
        {"subject": "Interview invitation"}, tool_context
    )
    message_ref = searched.data["messages"][0]["message_ref"]
    detail = await read.execute(
        {"message_ref": message_ref}, tool_context
    )
    drafted = await create.execute(
        {
            "storage_scope": "mock",
            "to": ["hr@example.test"],
            "subject": "Re: Interview invitation",
            "body_text": "Thank you. I confirm the proposed time.",
            "idempotency_key": "draft-key-00000001",
        },
        tool_context,
    )

    assert searched.ok
    assert detail.ok
    assert drafted.ok
    assert drafted.data["sent"] is False
    assert drafted.data["status"] == "draft_only"


async def test_create_draft_defaults_to_mock_for_mock_profile(tmp_path) -> None:
    configured = settings(tmp_path)
    tool = ToolRegistry(
        configured.tools.enabled, settings=configured
    ).get("email_create_draft")
    assert isinstance(tool, EmailCreateDraftTool)

    result = await tool.execute(
        {
            "to": ["hr@example.test"],
            "subject": "Mock draft",
            "body_text": "This stays inside the mock adapter.",
            "idempotency_key": "draft-default-mock-0001",
        },
        context(),
    )

    assert result.ok is True
    assert result.data["storage_scope"] == "mock"
    assert result.data["sent"] is False


async def test_real_profile_normalizes_mock_scope_to_local_draft(tmp_path) -> None:
    configured = real_settings(tmp_path)
    tool = ToolRegistry(
        configured.tools.enabled, settings=configured
    ).get("email_create_draft")
    assert isinstance(tool, EmailCreateDraftTool)

    result = await tool.execute(
        {
            "storage_scope": "mock",
            "to": ["candidate@example.test"],
            "subject": "SMTP preview",
            "body_text": "Preview this immutable local draft before sending.",
            "idempotency_key": "draft-real-local-00001",
        },
        context(),
    )

    assert result.ok is True
    assert result.data["profile"] == "qq"
    assert result.data["storage_scope"] == "local"
    assert result.data["sent"] is False


async def test_email_send_tool_rejects_missing_approval(tmp_path) -> None:
    configured = settings(tmp_path)
    registry = ToolRegistry(configured.tools.enabled, settings=configured)
    create = registry.get("email_create_draft")
    send = registry.get("email_send")
    assert isinstance(create, EmailCreateDraftTool)
    assert isinstance(send, EmailSendTool)
    tool_context = context()
    drafted = await create.execute(
        {
            "storage_scope": "mock",
            "to": ["hr@example.test"],
            "subject": "Re: Interview",
            "body_text": "Confirmed.",
            "idempotency_key": "draft-key-00000002",
        },
        tool_context,
    )

    result = await send.execute(
        {
            "draft_id": drafted.data["draft_id"],
            "expected_content_sha256": drafted.data["content_sha256"],
            "approval_id": "email-approval:forged",
            "idempotency_key": "send-key-00000001",
        },
        tool_context,
    )

    assert result.ok is False
    assert result.error_code == "email_approval_invalid"


def test_email_tool_risk_levels_and_schema_contract(tmp_path) -> None:
    configured = settings(tmp_path)
    registry = ToolRegistry(configured.tools.enabled, settings=configured)

    assert registry.get("email_search").risk_level == "read"
    assert registry.get("email_read").risk_level == "read"
    assert registry.get("email_create_draft").risk_level == "write"
    assert registry.get("email_send").risk_level == "external"
    assert (
        "storage_scope"
        not in registry.get("email_create_draft").input_schema["required"]
    )
    assert (
        registry.get("email_send")
        .input_schema["additionalProperties"]
        is False
    )


def test_email_tools_are_absent_when_disabled(tmp_path) -> None:
    configured = settings(tmp_path)
    registry = ToolRegistry([], settings=configured)

    assert registry.get("email_search") is None
    assert registry.email_manager is None
