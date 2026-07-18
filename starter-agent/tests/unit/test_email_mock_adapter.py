from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from starter_agent.tools.email.adapters.mock_fixture import (
    MockFixtureEmailAdapter,
)
from starter_agent.tools.email.errors import EmailError, EmailErrorCode
from starter_agent.tools.email.models import EmailSearchQuery, StoredDraft
from starter_agent.tools.email.store import (
    SQLiteEmailStore,
    draft_content_sha256,
    idempotency_hash,
)


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "email"


def adapter(tmp_path) -> MockFixtureEmailAdapter:
    return MockFixtureEmailAdapter(
        profile="mock",
        fixture_root=FIXTURES,
        store=SQLiteEmailStore("sqlite:///mock-email.db", tmp_path),
    )


async def test_mock_search_hits_hr_message_and_pages(tmp_path) -> None:
    mock = adapter(tmp_path)

    result = await mock.search(
        EmailSearchQuery(keywords=["AI Agent"], limit=1)
    )

    assert len(result.messages) == 1
    assert result.messages[0].subject == "AI Agent 工程师职位沟通"
    assert result.messages[0].message_ref.startswith("fixture-message:")


async def test_mock_read_is_peek_and_returns_thread_and_attachments(
    tmp_path,
) -> None:
    mock = adapter(tmp_path)
    before = await mock.search(
        EmailSearchQuery(subject="Interview invitation", unread_only=True)
    )

    message = await mock.read(
        "fixture-message:msg-interview-en-001",
        include_thread=True,
        thread_limit=10,
        max_body_chars=12_000,
    )
    after = await mock.search(
        EmailSearchQuery(subject="Interview invitation", unread_only=True)
    )

    assert message.headers.subject.startswith("Interview invitation")
    assert message.attachments[0].filename_masked == "*.ics"
    assert len(message.thread_messages) == 1
    assert len(after.messages) == len(before.messages)


async def test_mock_long_body_is_truncated(tmp_path) -> None:
    message = await adapter(tmp_path).read(
        "fixture-message:msg-long-001",
        include_thread=False,
        thread_limit=1,
        max_body_chars=1_000,
    )

    assert len(message.body_text) == 1_000
    assert message.is_truncated is True
    assert message.has_more is True
    assert message.source_ref


async def test_mock_draft_does_not_send_and_mock_send_is_explicit(
    tmp_path,
) -> None:
    mock = adapter(tmp_path)
    session_id = str(uuid4())
    digest = draft_content_sha256(
        to=["hr@example.test"],
        cc=[],
        bcc=[],
        subject="Re: Interview",
        body_text="Thank you",
        in_reply_to=None,
        attachment_sha256s=[],
    )
    draft = StoredDraft(
        draft_id=f"email-draft:{uuid4()}",
        session_id=session_id,
        profile="mock",
        storage_scope="mock",
        to=["hr@example.test"],
        subject="Re: Interview",
        body_text="Thank you",
        content_sha256=digest,
        idempotency_key_hash=idempotency_hash("draft-key-0000001"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    created = await mock.create_draft(draft)

    assert created.status == "draft_only"
    assert mock.send_calls == 0

    receipt = await mock.send_draft(
        created, idempotency_key="send-key-00000001"
    )

    assert receipt.status == "simulated_sent"
    assert receipt.external_delivery is False
    assert mock.send_calls == 1


async def test_mock_fixture_can_inject_stable_error(tmp_path) -> None:
    with pytest.raises(EmailError) as error:
        await adapter(tmp_path).search(
            EmailSearchQuery(keywords=["__timeout__"])
        )

    assert error.value.code == EmailErrorCode.PROVIDER_TIMEOUT
    assert error.value.retryable is True
