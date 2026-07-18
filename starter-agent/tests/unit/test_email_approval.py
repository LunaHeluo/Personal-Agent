from pathlib import Path
from uuid import uuid4

import pytest

from starter_agent.settings import EmailProfileConfig, EmailToolConfig
from starter_agent.tools.email.approval import EmailApprovalService
from starter_agent.tools.email.errors import EmailError, EmailErrorCode
from starter_agent.tools.email.manager import EmailManager
from starter_agent.tools.email.models import DraftCreateRequest
from starter_agent.tools.email.store import SQLiteEmailStore, stable_sha256


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def setup(tmp_path) -> tuple[EmailManager, EmailApprovalService]:
    manager = EmailManager(
        config=EmailToolConfig(
            active_profile="mock",
            profiles={
                "mock": EmailProfileConfig(
                    adapter="mock_fixture",
                    fixture_root="tests/fixtures/email",
                )
            },
        ),
        project_root=PROJECT_ROOT,
        store=SQLiteEmailStore("sqlite:///approval-email.db", tmp_path),
    )
    return manager, EmailApprovalService(manager)


async def draft(manager: EmailManager, session_id: str):
    return await manager.create_draft(
        DraftCreateRequest(
            profile="mock",
            storage_scope="mock",
            to=["hr@example.test"],
            subject="Re: Interview",
            body_text="Thank you. I confirm the interview time.",
            idempotency_key=f"draft-key-{uuid4()}",
        ),
        session_id=session_id,
    )


async def test_send_without_approval_is_rejected_before_adapter(tmp_path) -> None:
    manager, _ = setup(tmp_path)
    session_id = str(uuid4())
    item = await draft(manager, session_id)
    _, adapter = manager.adapter("mock")

    with pytest.raises(EmailError) as error:
        await manager.send(
            draft_id=item.draft_id,
            expected_content_sha256=item.content_sha256,
            approval_id="email-approval:forged",
            idempotency_key="send-key-00000001",
            session_id=session_id,
        )

    assert error.value.code == EmailErrorCode.APPROVAL_INVALID
    assert adapter.send_calls == 0  # type: ignore[attr-defined]


async def test_approval_requires_explicit_confirmation(tmp_path) -> None:
    manager, approvals = setup(tmp_path)
    session_id = str(uuid4())
    item = await draft(manager, session_id)
    challenge = approvals.create_challenge(
        item.draft_id, session_id=session_id
    )

    with pytest.raises(EmailError) as error:
        approvals.confirm(
            challenge.approval_id,
            session_id=session_id,
            confirmed=False,
        )

    assert error.value.code == EmailErrorCode.APPROVAL_REQUIRED
    assert approvals.get(
        challenge.approval_id, session_id=session_id
    ).status == "pending"


async def test_confirmed_mock_send_is_idempotent_and_consumes_approval(
    tmp_path,
) -> None:
    manager, approvals = setup(tmp_path)
    session_id = str(uuid4())
    item = await draft(manager, session_id)
    challenge = approvals.create_challenge(
        item.draft_id, session_id=session_id
    )
    approvals.confirm(
        challenge.approval_id,
        session_id=session_id,
        confirmed=True,
    )

    first = await manager.send(
        draft_id=item.draft_id,
        expected_content_sha256=item.content_sha256,
        approval_id=challenge.approval_id,
        idempotency_key="send-key-00000001",
        session_id=session_id,
    )
    second = await manager.send(
        draft_id=item.draft_id,
        expected_content_sha256=item.content_sha256,
        approval_id=challenge.approval_id,
        idempotency_key="send-key-00000001",
        session_id=session_id,
    )
    _, adapter = manager.adapter("mock")

    assert first.status == "simulated_sent"
    assert first.external_delivery is False
    assert second.source_ref == first.source_ref
    assert adapter.send_calls == 1  # type: ignore[attr-defined]
    assert approvals.get(
        challenge.approval_id, session_id=session_id
    ).status == "consumed"


async def test_changed_draft_invalidates_old_approval(tmp_path) -> None:
    manager, approvals = setup(tmp_path)
    session_id = str(uuid4())
    item = await draft(manager, session_id)
    challenge = approvals.create_challenge(
        item.draft_id, session_id=session_id
    )
    approvals.confirm(
        challenge.approval_id,
        session_id=session_id,
        confirmed=True,
    )
    changed = item.model_copy(
        update={
            "body_text": "Changed after approval",
            "content_sha256": stable_sha256("Changed after approval"),
        }
    )
    manager.store.save_draft(changed)

    with pytest.raises(EmailError) as error:
        await manager.send(
            draft_id=item.draft_id,
            expected_content_sha256=changed.content_sha256,
            approval_id=challenge.approval_id,
            idempotency_key="send-key-00000002",
            session_id=session_id,
        )

    assert error.value.code in {
        EmailErrorCode.APPROVAL_INVALID,
        EmailErrorCode.APPROVAL_REQUIRED,
    }


async def test_approval_cannot_cross_session(tmp_path) -> None:
    manager, approvals = setup(tmp_path)
    session_id = str(uuid4())
    item = await draft(manager, session_id)
    challenge = approvals.create_challenge(
        item.draft_id, session_id=session_id
    )

    with pytest.raises(EmailError) as error:
        approvals.confirm(
            challenge.approval_id,
            session_id=str(uuid4()),
            confirmed=True,
        )

    assert error.value.code == EmailErrorCode.APPROVAL_INVALID
