from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
from uuid import uuid4

import pytest

from starter_agent.settings import EmailProfileConfig
from starter_agent.tools.email.adapters.imap_smtp import (
    EnvironmentCredentialResolver,
    ImapSmtpEmailAdapter,
)
from starter_agent.tools.email.errors import EmailError, EmailErrorCode
from starter_agent.tools.email.models import EmailSearchQuery, StoredDraft
from starter_agent.tools.email.store import (
    SQLiteEmailStore,
    draft_content_sha256,
    idempotency_hash,
)


class FakeImap:
    def __init__(self, raw: bytes):
        self.raw = raw
        self.login_calls = []
        self.select_calls = []
        self.uid_calls = []
        self.append_calls = []

    def login(self, account, credential):
        self.login_calls.append((account, credential))
        return "OK", []

    def select(self, mailbox, readonly=False):
        self.select_calls.append((mailbox, readonly))
        return "OK", [b"1"]

    def uid(self, command, *args):
        self.uid_calls.append((command, args))
        if command == "search":
            return "OK", [b"42"]
        if command == "fetch":
            return "OK", [(b"42 (BODY[] {1})", self.raw), b")"]
        raise AssertionError(command)

    def append(self, mailbox, flags, date_time, raw):
        self.append_calls.append((mailbox, flags, raw))
        return "OK", [b"APPENDUID 1 43"]

    def logout(self):
        return "BYE", []


class FakeSmtp:
    def __init__(self, *, disconnect=False):
        self.disconnect = disconnect
        self.login_calls = []
        self.send_calls = 0
        self.sent_messages = []

    def login(self, account, credential):
        self.login_calls.append((account, credential))

    def send_message(self, message):
        self.send_calls += 1
        self.sent_messages.append(message)
        if self.disconnect:
            import smtplib

            raise smtplib.SMTPServerDisconnected("disconnected")
        return {}

    def quit(self):
        return None


def raw_message() -> bytes:
    message = EmailMessage()
    message["From"] = "Recruiter <recruiter@example.test>"
    message["To"] = "candidate@example.test"
    message["Subject"] = "Interview invitation"
    message["Date"] = "Fri, 17 Jul 2026 10:00:00 +0800"
    message["Message-ID"] = "<message-42@example.test>"
    message.set_content("Please confirm the interview time.")
    return message.as_bytes()


def profile(*, real_send=True, drafts_mailbox="Drafts"):
    return EmailProfileConfig(
        adapter="imap_smtp",
        mailbox_type="custom",
        account_env="EMAIL_ACCOUNT",
        auth={
            "type": "app_password",
            "credential_env": "EMAIL_CREDENTIAL",
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
        drafts_mailbox=drafts_mailbox,
        real_send_enabled=real_send,
    )


def credentials(name):
    return {
        "EMAIL_ACCOUNT": "candidate@example.test",
        "EMAIL_CREDENTIAL": "test-only-credential",
    }.get(name)


def adapter(tmp_path, imap, smtp, *, config=None, resolver=credentials):
    return ImapSmtpEmailAdapter(
        profile="real-test",
        config=config or profile(),
        store=SQLiteEmailStore("sqlite:///imap-test.db", tmp_path),
        credential_resolver=EnvironmentCredentialResolver(resolver),
        imap_factory=lambda _: imap,
        smtp_factory=lambda _: smtp,
    )


def draft() -> StoredDraft:
    content_hash = draft_content_sha256(
        to=["hr@example.test"],
        cc=[],
        bcc=[],
        subject="Re: Interview",
        body_text="Confirmed.",
        in_reply_to=None,
        attachment_sha256s=[],
    )
    return StoredDraft(
        draft_id=f"email-draft:{uuid4()}",
        session_id=str(uuid4()),
        profile="real-test",
        storage_scope="mailbox",
        to=["hr@example.test"],
        subject="Re: Interview",
        body_text="Confirmed.",
        content_sha256=content_hash,
        idempotency_key_hash=idempotency_hash("draft-key-0000001"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


async def test_imap_search_and_read_use_readonly_and_peek(tmp_path) -> None:
    imap = FakeImap(raw_message())
    real = adapter(tmp_path, imap, FakeSmtp())

    page = await real.search(
        EmailSearchQuery(subject="Interview", limit=5)
    )
    message = await real.read(
        page.messages[0].message_ref,
        include_thread=True,
        thread_limit=10,
        max_body_chars=12_000,
    )

    assert page.messages[0].subject == "Interview invitation"
    assert message.body_text == "Please confirm the interview time."
    assert all(readonly is True for _, readonly in imap.select_calls)
    assert any(
        call[0] == "fetch" and "BODY.PEEK[]" in str(call[1])
        for call in imap.uid_calls
    )


async def test_imap_search_uses_utf8_charset_for_chinese_subject(
    tmp_path,
) -> None:
    imap = FakeImap(raw_message())
    real = adapter(tmp_path, imap, FakeSmtp())

    await real.search(EmailSearchQuery(subject="求职交流"))

    _command, args = next(
        call for call in imap.uid_calls if call[0] == "search"
    )
    assert args[0] == "UTF-8"
    assert any(
        isinstance(arg, bytes) and "求职交流".encode("utf-8") in arg
        for arg in args[1:]
    )


async def test_imap_date_to_is_inclusive(tmp_path) -> None:
    imap = FakeImap(raw_message())
    real = adapter(tmp_path, imap, FakeSmtp())

    await real.search(
        EmailSearchQuery(
            sender="candidate@example.test",
            date_to=datetime(2026, 7, 18, 23, 59, tzinfo=UTC),
        )
    )

    _command, args = next(
        call for call in imap.uid_calls if call[0] == "search"
    )
    assert "19-Jul-2026" in args
    assert "18-Jul-2026" not in args


async def test_missing_credentials_fails_before_client_factory(tmp_path) -> None:
    factory_calls = []
    real = ImapSmtpEmailAdapter(
        profile="real-test",
        config=profile(),
        store=SQLiteEmailStore("sqlite:///imap-missing.db", tmp_path),
        credential_resolver=EnvironmentCredentialResolver(lambda _: None),
        imap_factory=lambda _: factory_calls.append(True),
        smtp_factory=lambda _: factory_calls.append(True),
    )

    with pytest.raises(EmailError) as error:
        await real.search(EmailSearchQuery(subject="Interview"))

    assert error.value.code == EmailErrorCode.MISSING_CREDENTIALS
    assert factory_calls == []


async def test_mailbox_draft_requires_explicit_drafts_mailbox(tmp_path) -> None:
    real = adapter(
        tmp_path,
        FakeImap(raw_message()),
        FakeSmtp(),
        config=profile(drafts_mailbox=None),
    )

    with pytest.raises(EmailError) as error:
        await real.create_draft(draft())

    assert error.value.code == EmailErrorCode.CAPABILITY_NOT_SUPPORTED


async def test_smtp_send_returns_real_receipt_and_is_idempotent(tmp_path) -> None:
    smtp = FakeSmtp()
    real = adapter(tmp_path, FakeImap(raw_message()), smtp)
    item = draft()

    first = await real.send_draft(
        item, idempotency_key="send-key-00000001"
    )
    second = await real.send_draft(
        item, idempotency_key="send-key-00000001"
    )

    assert first.status == "sent"
    assert first.external_delivery is True
    assert second.source_ref == first.source_ref
    assert smtp.send_calls == 1


async def test_smtp_disconnect_after_send_is_unknown_not_retryable(
    tmp_path,
) -> None:
    real = adapter(
        tmp_path,
        FakeImap(raw_message()),
        FakeSmtp(disconnect=True),
    )

    with pytest.raises(EmailError) as error:
        await real.send_draft(
            draft(), idempotency_key="send-key-00000002"
        )

    assert error.value.code == EmailErrorCode.SEND_STATUS_UNKNOWN
    assert error.value.retryable is False
    assert error.value.metadata["failure_stage"] == "smtp_send"
    assert error.value.metadata["failure_type"] == "SMTPServerDisconnected"


async def test_smtp_message_has_required_sender_headers(tmp_path) -> None:
    smtp = FakeSmtp()
    real = adapter(tmp_path, FakeImap(raw_message()), smtp)

    await real.send_draft(
        draft(), idempotency_key="send-key-00000003"
    )

    message = smtp.sent_messages[0]
    assert message["From"] == "candidate@example.test"
    assert message["Date"]
    assert message["Message-ID"]
