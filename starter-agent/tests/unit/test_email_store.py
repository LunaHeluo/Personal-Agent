from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from starter_agent.tools.email.errors import EmailError, EmailErrorCode
from starter_agent.tools.email.models import SendApproval, SendReceipt, StoredDraft
from starter_agent.tools.email.store import (
    SQLiteEmailStore,
    draft_content_sha256,
    idempotency_hash,
    recipient_sha256,
    stable_sha256,
)


def store(tmp_path) -> SQLiteEmailStore:
    return SQLiteEmailStore("sqlite:///email-test.db", tmp_path)


def draft(
    session_id: str,
    *,
    body: str = "Thank you for the interview invitation.",
    key: str = "draft-key-000001",
) -> StoredDraft:
    to = ["hr@example.test"]
    digest = draft_content_sha256(
        to=to,
        cc=[],
        bcc=[],
        subject="Re: Interview",
        body_text=body,
        in_reply_to=None,
        attachment_sha256s=[],
    )
    now = datetime.now(UTC)
    return StoredDraft(
        draft_id=f"email-draft:{uuid4()}",
        session_id=session_id,
        profile="mock",
        storage_scope="mock",
        to=to,
        subject="Re: Interview",
        body_text=body,
        content_sha256=digest,
        idempotency_key_hash=idempotency_hash(key),
        created_at=now,
        updated_at=now,
    )


def approval_for(item: StoredDraft, *, status: str = "approved") -> SendApproval:
    return SendApproval(
        approval_id=f"email-approval:{uuid4()}",
        session_id=item.session_id,
        profile=item.profile,
        draft_id=item.draft_id,
        content_sha256=item.content_sha256,
        recipient_sha256=recipient_sha256(item.to, item.cc, item.bcc),
        status=status,
        approved_at=datetime.now(UTC) if status == "approved" else None,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )


def test_reference_is_opaque_and_bound_to_session_and_profile(tmp_path) -> None:
    email_store = store(tmp_path)
    session_id = str(uuid4())
    reference = email_store.create_reference(
        session_id=session_id,
        profile="mock",
        object_type="message",
        object_id="provider-uid-42",
    )

    assert "provider-uid-42" not in reference
    assert (
        email_store.resolve_reference(
            reference,
            session_id=session_id,
            profile="mock",
            object_type="message",
        )
        == "provider-uid-42"
    )
    with pytest.raises(EmailError) as error:
        email_store.resolve_reference(
            reference,
            session_id=str(uuid4()),
            profile="mock",
            object_type="message",
        )
    assert error.value.code == EmailErrorCode.MESSAGE_NOT_FOUND


def test_reference_expiry_is_enforced(tmp_path) -> None:
    email_store = store(tmp_path)
    session_id = str(uuid4())
    reference = email_store.create_reference(
        session_id=session_id,
        profile="mock",
        object_type="message",
        object_id="uid",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    with pytest.raises(EmailError):
        email_store.resolve_reference(
            reference,
            session_id=session_id,
            profile="mock",
            object_type="message",
        )


def test_draft_fingerprint_changes_with_content() -> None:
    first = draft_content_sha256(
        to=["hr@example.test"],
        cc=[],
        bcc=[],
        subject="Subject",
        body_text="First",
        in_reply_to=None,
        attachment_sha256s=[],
    )
    second = draft_content_sha256(
        to=["hr@example.test"],
        cc=[],
        bcc=[],
        subject="Subject",
        body_text="Second",
        in_reply_to=None,
        attachment_sha256s=[],
    )

    assert first != second


def test_changed_draft_invalidates_existing_approval(tmp_path) -> None:
    email_store = store(tmp_path)
    session_id = str(uuid4())
    first = email_store.save_draft(draft(session_id))
    accepted = email_store.save_approval(approval_for(first))
    changed = first.model_copy(
        update={
            "body_text": "Changed content",
            "content_sha256": stable_sha256("Changed content"),
            "updated_at": datetime.now(UTC),
        }
    )

    email_store.save_draft(changed)
    reread = email_store.get_approval(
        accepted.approval_id, session_id=session_id
    )

    assert reread.status == "invalidated"


def test_idempotency_same_request_reuses_and_different_request_conflicts(
    tmp_path,
) -> None:
    email_store = store(tmp_path)
    email_store.save_idempotency(
        "create_draft", "same-key-0000001", "request-a", {"draft_id": "draft-1"}
    )

    assert email_store.get_idempotency(
        "create_draft", "same-key-0000001", "request-a"
    ) == {"draft_id": "draft-1"}
    with pytest.raises(EmailError) as error:
        email_store.get_idempotency(
            "create_draft", "same-key-0000001", "request-b"
        )
    assert error.value.code == EmailErrorCode.IDEMPOTENCY_CONFLICT


def test_receipt_is_idempotent(tmp_path) -> None:
    email_store = store(tmp_path)
    receipt = SendReceipt(
        delivery_mode="mock",
        status="simulated_sent",
        external_delivery=False,
        content_sha256="a" * 64,
        recipient_count=1,
        idempotency_key_hash=idempotency_hash("send-key-00000001"),
        source_ref="email-send-receipt:one",
    )

    first = email_store.save_receipt(
        "email-draft:one", "send-key-00000001", receipt
    )
    second = email_store.save_receipt(
        "email-draft:one",
        "send-key-00000001",
        receipt.model_copy(update={"source_ref": "different"}),
    )

    assert second.source_ref == first.source_ref
