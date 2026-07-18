from pathlib import Path
from uuid import uuid4

import pytest

from starter_agent.settings import EmailProfileConfig, EmailToolConfig
from starter_agent.tools.email.errors import EmailError, EmailErrorCode
from starter_agent.tools.email.manager import EmailManager
from starter_agent.tools.email.models import DraftCreateRequest
from starter_agent.tools.email.store import SQLiteEmailStore


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def manager(tmp_path) -> EmailManager:
    return EmailManager(
        config=EmailToolConfig(
            active_profile="mock",
            attachment_root="tests/fixtures/email/attachments",
            profiles={
                "mock": EmailProfileConfig(
                    adapter="mock_fixture",
                    fixture_root="tests/fixtures/email",
                )
            },
        ),
        project_root=PROJECT_ROOT,
        store=SQLiteEmailStore("sqlite:///draft-email.db", tmp_path),
    )


def request(**updates) -> DraftCreateRequest:
    values = {
        "profile": "mock",
        "storage_scope": "mock",
        "to": ["hr@example.test"],
        "subject": "Re: Interview invitation",
        "body_text": "Thank you. The proposed time works for me.",
        "idempotency_key": "draft-key-00000001",
    }
    values.update(updates)
    return DraftCreateRequest(**values)


async def test_create_mock_draft_never_sends(tmp_path) -> None:
    email_manager = manager(tmp_path)
    created = await email_manager.create_draft(
        request(), session_id=str(uuid4())
    )
    _, adapter = email_manager.adapter("mock")

    assert created.status == "draft_only"
    assert created.storage_scope == "mock"
    assert created.content_sha256
    assert adapter.send_calls == 0  # type: ignore[attr-defined]


async def test_draft_creation_is_idempotent(tmp_path) -> None:
    email_manager = manager(tmp_path)
    session_id = str(uuid4())

    first = await email_manager.create_draft(
        request(), session_id=session_id
    )
    second = await email_manager.create_draft(
        request(), session_id=session_id
    )

    assert second.draft_id == first.draft_id


@pytest.mark.parametrize(
    ("updates", "error_code"),
    [
        (
            {"to": ["not-an-email"]},
            EmailErrorCode.INVALID_RECIPIENT,
        ),
        (
            {"body_text": "Hello [招聘方姓名待补充]"},
            EmailErrorCode.PLACEHOLDER_PRESENT,
        ),
        (
            {"attachment_refs": ["missing.pdf"]},
            EmailErrorCode.ATTACHMENT_NOT_FOUND,
        ),
    ],
)
async def test_draft_blocks_deterministic_errors(
    tmp_path, updates, error_code
) -> None:
    with pytest.raises(EmailError) as error:
        await manager(tmp_path).create_draft(
            request(**updates), session_id=str(uuid4())
        )

    assert error.value.code == error_code


async def test_draft_attachment_has_verified_fingerprint(tmp_path) -> None:
    created = await manager(tmp_path).create_draft(
        request(attachment_refs=["resume.txt"]),
        session_id=str(uuid4()),
    )

    assert len(created.attachments) == 1
    assert created.attachments[0].size_bytes > 0
    assert len(created.attachments[0].sha256) == 64


async def test_mailbox_scope_does_not_silently_fall_back(tmp_path) -> None:
    with pytest.raises(EmailError) as error:
        await manager(tmp_path).create_draft(
            request(storage_scope="mailbox"),
            session_id=str(uuid4()),
        )

    assert error.value.code == EmailErrorCode.CAPABILITY_NOT_SUPPORTED
