from pathlib import Path
from uuid import uuid4

from starter_agent.settings import AgentSettings, ProviderConfig, ToolsConfig
from starter_agent.tools.base import ToolContext
from starter_agent.tools.email.approval import EmailApprovalService
from starter_agent.tools.registry import ToolRegistry


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def registry(tmp_path) -> ToolRegistry:
    settings = AgentSettings(
        providers={"mock": ProviderConfig(type="mock")},
        project_root=PROJECT_ROOT,
        app={"database_url": f"sqlite:///{tmp_path / 'e2e-email.db'}"},
        tools=ToolsConfig.model_validate(
            {
                "enabled": [
                    "email_search",
                    "email_read",
                    "email_create_draft",
                    "email_send",
                ],
                "allow_risk_levels": ["read", "write", "external"],
                "email": {
                    "active_profile": "mock",
                    "body_max_chars": 1000,
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
    return ToolRegistry(settings.tools.enabled, settings=settings)


async def test_mock_email_suite_end_to_end(tmp_path) -> None:
    tools = registry(tmp_path)
    context = ToolContext(session_id=uuid4(), turn_id=uuid4())
    searched = await tools.get("email_search").execute(
        {"subject": "Long interview"}, context
    )
    message_ref = searched.data["messages"][0]["message_ref"]
    read = await tools.get("email_read").execute(
        {"message_ref": message_ref, "max_body_chars": 5000}, context
    )
    created = await tools.get("email_create_draft").execute(
        {
            "storage_scope": "mock",
            "to": ["hr@example.test"],
            "subject": "Re: Long interview preparation details",
            "body_text": "Thank you. I have reviewed the details.",
            "in_reply_to": message_ref,
            "evidence_source_refs": [
                read.data["message"]["source_ref"]
            ],
            "idempotency_key": "draft-e2e-key-00001",
        },
        context,
    )
    rejected = await tools.get("email_send").execute(
        {
            "draft_id": created.data["draft_id"],
            "expected_content_sha256": created.data["content_sha256"],
            "approval_id": "email-approval:forged",
            "idempotency_key": "send-e2e-key-000001",
        },
        context,
    )
    approval_service = EmailApprovalService(tools.email_manager)
    challenge = approval_service.create_challenge(
        created.data["draft_id"], session_id=str(context.session_id)
    )
    approval_service.confirm(
        challenge.approval_id,
        session_id=str(context.session_id),
        confirmed=True,
    )
    sent = await tools.get("email_send").execute(
        {
            "draft_id": created.data["draft_id"],
            "expected_content_sha256": created.data["content_sha256"],
            "approval_id": challenge.approval_id,
            "idempotency_key": "send-e2e-key-000001",
        },
        context,
    )

    assert searched.ok
    assert read.ok
    assert read.data["message"]["is_truncated"] is True
    assert read.data["message"]["has_more"] is True
    assert read.data["message"]["source_ref"]
    assert created.ok
    assert created.data["sent"] is False
    assert rejected.error_code == "email_approval_invalid"
    assert sent.ok
    assert sent.data["status"] == "simulated_sent"
    assert sent.data["external_delivery"] is False


async def test_missing_real_profile_returns_stable_error(tmp_path) -> None:
    tools = registry(tmp_path)
    result = await tools.get("email_search").execute(
        {"profile": "not-configured", "subject": "Interview"},
        ToolContext(session_id=uuid4(), turn_id=uuid4()),
    )

    assert result.ok is False
    assert result.error_code == "email_profile_not_found"
