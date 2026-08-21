from __future__ import annotations

import asyncio
import base64
import imaplib
import smtplib
import socket
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage as MimeEmailMessage
from email.parser import BytesParser
from email.utils import (
    format_datetime,
    getaddresses,
    make_msgid,
    parsedate_to_datetime,
)
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from starter_agent.settings import EmailConnectionConfig, EmailProfileConfig
from starter_agent.tools.email.adapters.mock_fixture import mask_address
from starter_agent.tools.email.errors import EmailError, EmailErrorCode
from starter_agent.tools.email.models import (
    EmailAttachmentMeta,
    EmailCapabilities,
    EmailHeaders,
    EmailMessage,
    EmailMessageSummary,
    EmailReplyContext,
    EmailSearchPage,
    EmailSearchQuery,
    SendReceipt,
    StoredDraft,
)
from starter_agent.tools.email.store import EmailStore, idempotency_hash


@dataclass(frozen=True)
class EmailCredentials:
    account: str
    auth_type: str
    credential: str | None = None
    oauth_client_id: str | None = None
    oauth_refresh_token: str | None = None


class OAuthTokenProvider(Protocol):
    def access_token(self, credentials: EmailCredentials) -> str: ...


class EnvironmentCredentialResolver:
    def __init__(self, resolver: Callable[[str], str | None]) -> None:
        self.resolver = resolver

    def resolve(self, config: EmailProfileConfig) -> EmailCredentials:
        assert config.account_env is not None
        account = self.resolver(config.account_env)
        if not account or config.auth is None:
            raise EmailError(
                EmailErrorCode.MISSING_CREDENTIALS,
                "邮箱账号或认证配置缺失",
                metadata={"account_env": config.account_env},
            )
        auth = config.auth
        if auth.type == "oauth":
            client_id = (
                self.resolver(auth.oauth_client_id_env)
                if auth.oauth_client_id_env
                else None
            )
            refresh_token = (
                self.resolver(auth.oauth_refresh_token_env)
                if auth.oauth_refresh_token_env
                else None
            )
            if not client_id or not refresh_token:
                raise EmailError(
                    EmailErrorCode.MISSING_CREDENTIALS,
                    "邮箱 OAuth 凭据缺失",
                    metadata={
                        "oauth_client_id_env": auth.oauth_client_id_env,
                        "oauth_refresh_token_env": auth.oauth_refresh_token_env,
                    },
                )
            return EmailCredentials(
                account=account,
                auth_type=auth.type,
                oauth_client_id=client_id,
                oauth_refresh_token=refresh_token,
            )
        credential = (
            self.resolver(auth.credential_env)
            if auth.credential_env
            else None
        )
        if not credential:
            raise EmailError(
                EmailErrorCode.MISSING_CREDENTIALS,
                "邮箱应用专用密码或授权码缺失",
                metadata={"credential_env": auth.credential_env},
            )
        return EmailCredentials(
            account=account,
            auth_type=auth.type,
            credential=credential,
        )


class ImapSmtpEmailAdapter:
    def __init__(
        self,
        *,
        profile: str,
        config: EmailProfileConfig,
        store: EmailStore,
        credential_resolver: EnvironmentCredentialResolver,
        oauth_token_provider: OAuthTokenProvider | None = None,
        imap_factory: Callable[[EmailConnectionConfig], Any] | None = None,
        smtp_factory: Callable[[EmailConnectionConfig], Any] | None = None,
        timeout_seconds: float = 20,
    ) -> None:
        self.profile = profile
        self.config = config
        self.store = store
        self.credential_resolver = credential_resolver
        self.oauth_token_provider = oauth_token_provider
        self.imap_factory = imap_factory or self._default_imap_factory
        self.smtp_factory = smtp_factory or self._default_smtp_factory
        self.timeout_seconds = timeout_seconds

    async def capabilities(self) -> EmailCapabilities:
        return EmailCapabilities(
            search=self.config.imap is not None,
            read_peek=self.config.imap is not None,
            local_draft=True,
            mailbox_draft=bool(
                self.config.imap and self.config.drafts_mailbox
            ),
            simulated_send=False,
            real_send=self.config.smtp is not None,
        )

    async def search(self, query: EmailSearchQuery) -> EmailSearchPage:
        return await self._run(self._search_sync, query, operation="search")

    async def read(
        self,
        message_ref: str,
        *,
        include_thread: bool,
        thread_limit: int,
        max_body_chars: int,
    ) -> EmailMessage:
        return await self._run(
            self._read_sync,
            message_ref,
            max_body_chars,
            operation="read",
        )

    async def create_draft(self, draft: StoredDraft) -> StoredDraft:
        if not self.config.drafts_mailbox:
            raise EmailError(
                EmailErrorCode.CAPABILITY_NOT_SUPPORTED,
                "当前 IMAP profile 未配置 Drafts mailbox",
            )
        return await self._run(
            self._create_draft_sync, draft, operation="create_draft"
        )

    async def send_draft(
        self,
        draft: StoredDraft,
        *,
        idempotency_key: str,
    ) -> SendReceipt:
        existing = self.store.find_receipt(draft.draft_id, idempotency_key)
        if existing is not None:
            return existing
        try:
            receipt = await asyncio.wait_for(
                asyncio.to_thread(
                    self._send_sync, draft, idempotency_key
                ),
                timeout=self.timeout_seconds,
            )
        except TimeoutError as exc:
            raise EmailError(
                EmailErrorCode.SEND_STATUS_UNKNOWN,
                "SMTP 发送超时，当前无法确认是否已发送",
                retryable=False,
                metadata={
                    "profile": self.profile,
                    "operation": "send",
                    "failure_stage": "smtp_send",
                    "failure_type": type(exc).__name__,
                },
            ) from exc
        return self.store.save_receipt(
            draft.draft_id, idempotency_key, receipt
        )

    async def find_send_result(
        self,
        draft_id: str,
        *,
        idempotency_key: str,
    ) -> SendReceipt | None:
        return self.store.find_receipt(draft_id, idempotency_key)

    async def _run(
        self,
        function: Callable[..., Any],
        *args: Any,
        operation: str,
    ) -> Any:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(function, *args),
                timeout=self.timeout_seconds,
            )
        except EmailError:
            raise
        except TimeoutError as exc:
            raise EmailError(
                EmailErrorCode.PROVIDER_TIMEOUT,
                f"邮箱 {operation} 操作超时",
                retryable=True,
                metadata={"profile": self.profile, "operation": operation},
            ) from exc
        except (imaplib.IMAP4.error, smtplib.SMTPAuthenticationError) as exc:
            raise EmailError(
                EmailErrorCode.AUTHENTICATION_FAILED,
                "邮箱认证失败",
                metadata={"profile": self.profile, "operation": operation},
            ) from exc
        except (OSError, socket.error, smtplib.SMTPException) as exc:
            raise EmailError(
                EmailErrorCode.TRANSPORT_ERROR,
                "邮箱连接或协议操作失败",
                retryable=True,
                metadata={"profile": self.profile, "operation": operation},
            ) from exc

    def _search_sync(self, query: EmailSearchQuery) -> EmailSearchPage:
        credentials = self.credential_resolver.resolve(self.config)
        client = self._open_imap()
        try:
            self._authenticate_imap(client, credentials)
            self._select(client, query.mailbox)
            charset, criteria = self._search_criteria(query)
            status, data = client.uid("search", charset, *criteria)
            if status != "OK":
                raise EmailError(
                    EmailErrorCode.MAILBOX_UNAVAILABLE,
                    "IMAP 搜索失败",
                    retryable=True,
                )
            identifiers = self._search_ids(data)
            offset = self._cursor_offset(query.cursor, len(identifiers))
            identifiers = list(reversed(identifiers))
            selected = identifiers[offset : offset + query.limit]
            messages = [
                self._summary_from_bytes(
                    identifier,
                    self._fetch_message(client, identifier),
                    query.mailbox,
                )
                for identifier in selected
            ]
            next_offset = offset + len(selected)
            has_more = next_offset < len(identifiers)
            return EmailSearchPage(
                profile=self.profile,
                messages=messages,
                next_cursor=str(next_offset) if has_more else None,
                is_truncated=False,
                has_more=has_more,
                source_ref=f"imap-search:{query.mailbox}:{uuid4()}",
            )
        finally:
            self._close_imap(client)

    def _read_sync(
        self, message_ref: str, max_body_chars: int
    ) -> EmailMessage:
        mailbox, uid = self._parse_message_ref(message_ref)
        credentials = self.credential_resolver.resolve(self.config)
        client = self._open_imap()
        try:
            self._authenticate_imap(client, credentials)
            self._select(client, mailbox)
            raw = self._fetch_message(client, uid)
            parsed = BytesParser(policy=policy.default).parsebytes(raw)
            return self._message_from_mime(
                uid, mailbox, parsed, max_body_chars
            )
        finally:
            self._close_imap(client)

    def _create_draft_sync(self, draft: StoredDraft) -> StoredDraft:
        assert self.config.drafts_mailbox is not None
        credentials = self.credential_resolver.resolve(self.config)
        client = self._open_imap()
        try:
            self._authenticate_imap(client, credentials)
            message = self._mime_from_draft(
                draft, sender=credentials.account
            )
            status, _ = client.append(
                self.config.drafts_mailbox,
                "(\\Draft)",
                None,
                message.as_bytes(policy=policy.SMTP),
            )
            if status != "OK":
                raise EmailError(
                    EmailErrorCode.TRANSPORT_ERROR,
                    "邮箱草稿创建失败",
                    retryable=True,
                )
            created = draft.model_copy(
                update={
                    "provider_draft_id": f"imap-draft:{uuid4()}",
                    "status": "draft_only",
                    "updated_at": datetime.now(UTC),
                }
            )
            return self.store.save_draft(created)
        finally:
            self._close_imap(client)

    def _send_sync(
        self, draft: StoredDraft, idempotency_key: str
    ) -> SendReceipt:
        if self.config.smtp is None:
            raise EmailError(
                EmailErrorCode.CAPABILITY_NOT_SUPPORTED,
                "当前 profile 未配置 SMTP",
            )
        credentials = self.credential_resolver.resolve(self.config)
        client = self.smtp_factory(self.config.smtp)
        send_attempted = False
        try:
            self._authenticate_smtp(client, credentials)
            message = self._mime_from_draft(
                draft, sender=credentials.account
            )
            send_attempted = True
            refused = client.send_message(message)
            if refused:
                raise EmailError(
                    EmailErrorCode.SEND_REJECTED,
                    "SMTP 拒绝了一个或多个收件地址",
                )
            return SendReceipt(
                delivery_mode="real",
                status="sent",
                external_delivery=True,
                message_ref=f"smtp-message:{message['Message-ID'] or uuid4()}",
                thread_ref=draft.in_reply_to,
                sent_at=datetime.now(UTC),
                content_sha256=draft.content_sha256,
                recipient_count=len(draft.to) + len(draft.cc) + len(draft.bcc),
                idempotency_key_hash=idempotency_hash(idempotency_key),
                source_ref=f"smtp-send-receipt:{uuid4()}",
            )
        except EmailError:
            raise
        except (TimeoutError, OSError, smtplib.SMTPServerDisconnected) as exc:
            if send_attempted:
                raise EmailError(
                    EmailErrorCode.SEND_STATUS_UNKNOWN,
                    "SMTP 连接中断，无法确认是否已发送",
                    retryable=False,
                    metadata={
                        "profile": self.profile,
                        "operation": "send",
                        "failure_stage": "smtp_send",
                        "failure_type": type(exc).__name__,
                    },
                ) from exc
            raise
        finally:
            try:
                client.quit()
            except Exception:
                pass

    def _open_imap(self) -> Any:
        if self.config.imap is None:
            raise EmailError(
                EmailErrorCode.CAPABILITY_NOT_SUPPORTED,
                "当前 profile 未配置 IMAP",
            )
        return self.imap_factory(self.config.imap)

    def _authenticate_imap(
        self, client: Any, credentials: EmailCredentials
    ) -> None:
        if credentials.auth_type == "oauth":
            token = self._oauth_access_token(credentials)
            auth = (
                f"user={credentials.account}\x01auth=Bearer {token}\x01\x01"
            ).encode("utf-8")
            client.authenticate("XOAUTH2", lambda _: auth)
        else:
            client.login(credentials.account, credentials.credential)

    def _authenticate_smtp(
        self, client: Any, credentials: EmailCredentials
    ) -> None:
        if credentials.auth_type == "oauth":
            token = self._oauth_access_token(credentials)
            auth = base64.b64encode(
                (
                    f"user={credentials.account}\x01"
                    f"auth=Bearer {token}\x01\x01"
                ).encode("utf-8")
            ).decode("ascii")
            code, _ = client.docmd("AUTH", f"XOAUTH2 {auth}")
            if code not in {235, 250}:
                raise EmailError(
                    EmailErrorCode.AUTHENTICATION_FAILED,
                    "SMTP OAuth 认证失败",
                )
        else:
            client.login(credentials.account, credentials.credential)

    def _oauth_access_token(self, credentials: EmailCredentials) -> str:
        if self.oauth_token_provider is None:
            raise EmailError(
                EmailErrorCode.CAPABILITY_NOT_SUPPORTED,
                "当前 profile 尚未配置 OAuth token provider",
            )
        return self.oauth_token_provider.access_token(credentials)

    @staticmethod
    def _select(client: Any, mailbox: str) -> None:
        status, _ = client.select(mailbox, readonly=True)
        if status != "OK":
            raise EmailError(
                EmailErrorCode.MAILBOX_UNAVAILABLE,
                "邮箱目录不可用",
            )

    @staticmethod
    def _search_criteria(
        query: EmailSearchQuery,
    ) -> tuple[str | None, list[str | bytes]]:
        text_values = [
            value
            for value in (
                query.sender,
                query.recipient,
                query.subject,
                *query.keywords,
            )
            if value
        ]
        charset = (
            "UTF-8"
            if any(not value.isascii() for value in text_values)
            else None
        )
        criteria: list[str | bytes] = []

        def quoted(value: str) -> str | bytes:
            result = ImapSmtpEmailAdapter._quote(value)
            return result.encode("utf-8") if charset else result

        for name, value in (
            ("FROM", query.sender),
            ("TO", query.recipient),
            ("SUBJECT", query.subject),
        ):
            if value:
                criteria.extend([name, quoted(value)])
        for keyword in query.keywords:
            criteria.extend(["TEXT", quoted(keyword)])
        if query.date_from:
            criteria.extend(["SINCE", query.date_from.strftime("%d-%b-%Y")])
        if query.date_to:
            inclusive_end = query.date_to + timedelta(days=1)
            criteria.extend(["BEFORE", inclusive_end.strftime("%d-%b-%Y")])
        if query.unread_only:
            criteria.append("UNSEEN")
        if not criteria:
            raise EmailError(
                EmailErrorCode.QUERY_INVALID,
                "请至少提供一个邮箱搜索条件",
            )
        return charset, criteria

    @staticmethod
    def _quote(value: str) -> str:
        if any(character in value for character in ("\r", "\n", '"', "\\")):
            raise EmailError(
                EmailErrorCode.QUERY_INVALID,
                "邮箱查询包含不安全字符",
            )
        return f'"{value}"'

    @staticmethod
    def _search_ids(data: Any) -> list[str]:
        if not data or not isinstance(data[0], (bytes, bytearray)):
            return []
        return [
            value.decode("ascii")
            for value in bytes(data[0]).split()
            if value.isdigit()
        ]

    @staticmethod
    def _cursor_offset(cursor: str | None, total: int) -> int:
        try:
            offset = int(cursor or "0")
        except ValueError as exc:
            raise EmailError(
                EmailErrorCode.CURSOR_INVALID,
                "IMAP 分页游标无效",
            ) from exc
        if offset < 0 or offset > total:
            raise EmailError(
                EmailErrorCode.CURSOR_INVALID,
                "IMAP 分页游标无效",
            )
        return offset

    @staticmethod
    def _fetch_message(client: Any, uid: str) -> bytes:
        status, data = client.uid("fetch", uid, "(BODY.PEEK[])")
        if status != "OK":
            raise EmailError(
                EmailErrorCode.MESSAGE_NOT_FOUND,
                "IMAP 邮件不存在或无法读取",
            )
        for item in data or []:
            if (
                isinstance(item, tuple)
                and len(item) >= 2
                and isinstance(item[1], (bytes, bytearray))
            ):
                return bytes(item[1])
        raise EmailError(
            EmailErrorCode.PARSE_FAILED,
            "IMAP 邮件内容无法解析",
        )

    def _summary_from_bytes(
        self, uid: str, raw: bytes, mailbox: str
    ) -> EmailMessageSummary:
        parsed = BytesParser(policy=policy.default).parsebytes(raw)
        body = self._plain_text(parsed)
        from_values = getaddresses(parsed.get_all("from", []))
        sender = from_values[0][1] if from_values else ""
        return EmailMessageSummary(
            message_ref=f"imap-message:{mailbox}:{uid}",
            thread_ref=self._thread_ref(parsed),
            from_masked=mask_address(sender),
            to_me=True,
            subject=self._decoded_header(parsed.get("subject", "")),
            sent_at=self._message_date(parsed),
            snippet=body[:240],
            flags=[],
            source_ref=f"imap-source:{mailbox}:{uid}",
        )

    def _message_from_mime(
        self,
        uid: str,
        mailbox: str,
        parsed: Any,
        max_body_chars: int,
    ) -> EmailMessage:
        body = self._plain_text(parsed)
        attachments: list[EmailAttachmentMeta] = []
        for index, part in enumerate(parsed.walk() if parsed.is_multipart() else []):
            filename = part.get_filename()
            if not filename:
                continue
            payload = part.get_payload(decode=True) or b""
            attachments.append(
                EmailAttachmentMeta(
                    attachment_ref=f"imap-attachment:{mailbox}:{uid}:{index}",
                    filename_masked=f"*{Path(filename).suffix}" if Path(filename).suffix else "***",
                    content_type=part.get_content_type(),
                    size_bytes=len(payload),
                    sha256=None,
                )
            )
        from_addresses = getaddresses(parsed.get_all("from", []))
        to_addresses = getaddresses(parsed.get_all("to", []))
        cc_addresses = getaddresses(parsed.get_all("cc", []))
        references = str(parsed.get("references", "")).split()
        return EmailMessage(
            message_ref=f"imap-message:{mailbox}:{uid}",
            thread_ref=self._thread_ref(parsed),
            headers=EmailHeaders(
                from_masked=mask_address(
                    from_addresses[0][1] if from_addresses else ""
                ),
                to_masked=[mask_address(value) for _, value in to_addresses],
                cc_masked=[mask_address(value) for _, value in cc_addresses],
                subject=self._decoded_header(parsed.get("subject", "")),
                date=self._message_date(parsed),
                reply_to_masked=(
                    mask_address(str(parsed.get("reply-to")))
                    if parsed.get("reply-to")
                    else None
                ),
            ),
            body_text=body[:max_body_chars],
            attachments=attachments,
            reply_context=EmailReplyContext(
                in_reply_to=parsed.get("in-reply-to"),
                references_count=len(references),
                thread_position=len(references) + 1,
            ),
            is_truncated=len(body) > max_body_chars,
            has_more=len(body) > max_body_chars,
            source_ref=f"imap-source:{mailbox}:{uid}",
        )

    @staticmethod
    def _plain_text(parsed: Any) -> str:
        if parsed.is_multipart():
            plain_parts: list[str] = []
            html_fallback: list[str] = []
            for part in parsed.walk():
                if part.get_content_disposition() == "attachment":
                    continue
                content_type = part.get_content_type()
                if content_type not in {"text/plain", "text/html"}:
                    continue
                try:
                    content = part.get_content()
                except (LookupError, UnicodeError):
                    continue
                if content_type == "text/plain":
                    plain_parts.append(str(content))
                else:
                    html_fallback.append(ImapSmtpEmailAdapter._html_to_text(str(content)))
            return "\n".join(plain_parts or html_fallback).strip()
        try:
            content = parsed.get_content()
        except (LookupError, UnicodeError) as exc:
            raise EmailError(
                EmailErrorCode.PARSE_FAILED,
                "邮件正文编码无法解析",
            ) from exc
        if parsed.get_content_type() == "text/html":
            return ImapSmtpEmailAdapter._html_to_text(str(content))
        return str(content).strip()

    @staticmethod
    def _html_to_text(value: str) -> str:
        import re

        without_active = re.sub(
            r"(?is)<(script|style).*?>.*?</\1>", " ", value
        )
        without_tags = re.sub(r"(?s)<[^>]+>", " ", without_active)
        return re.sub(r"\s+", " ", without_tags).strip()

    @staticmethod
    def _decoded_header(value: str) -> str:
        try:
            return str(make_header(decode_header(value)))
        except (LookupError, UnicodeError):
            return value

    @staticmethod
    def _message_date(parsed: Any) -> datetime:
        try:
            value = parsedate_to_datetime(parsed.get("date"))
        except (TypeError, ValueError):
            value = None
        if value is None:
            return datetime.now(UTC)
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    @staticmethod
    def _thread_ref(parsed: Any) -> str | None:
        value = parsed.get("references") or parsed.get("message-id")
        return f"imap-thread:{value}" if value else None

    @staticmethod
    def _parse_message_ref(value: str) -> tuple[str, str]:
        parts = value.split(":", 2)
        if len(parts) != 3 or parts[0] != "imap-message":
            raise EmailError(
                EmailErrorCode.MESSAGE_NOT_FOUND,
                "IMAP 邮件引用无效",
            )
        return parts[1], parts[2]

    @staticmethod
    def _mime_from_draft(
        draft: StoredDraft,
        *,
        sender: str,
    ) -> MimeEmailMessage:
        message = MimeEmailMessage()
        message["From"] = sender
        message["To"] = ", ".join(draft.to)
        if draft.cc:
            message["Cc"] = ", ".join(draft.cc)
        if draft.bcc:
            message["Bcc"] = ", ".join(draft.bcc)
        message["Subject"] = draft.subject
        if draft.in_reply_to:
            message["In-Reply-To"] = draft.in_reply_to
        message["Date"] = format_datetime(datetime.now(UTC))
        sender_domain = sender.rpartition("@")[2] or None
        message["Message-ID"] = make_msgid(domain=sender_domain)
        message.set_content(draft.body_text)
        for attachment in draft.attachments:
            if not attachment.path:
                raise EmailError(
                    EmailErrorCode.ATTACHMENT_NOT_FOUND,
                    "附件缺少可读取路径",
                )
            path = Path(attachment.path)
            try:
                content = path.read_bytes()
            except OSError as exc:
                raise EmailError(
                    EmailErrorCode.ATTACHMENT_NOT_FOUND,
                    "附件无法读取",
                ) from exc
            message.add_attachment(
                content,
                maintype="application",
                subtype="octet-stream",
                filename=path.name,
            )
        return message

    @staticmethod
    def _close_imap(client: Any) -> None:
        try:
            client.logout()
        except Exception:
            pass

    @staticmethod
    def _default_imap_factory(config: EmailConnectionConfig) -> Any:
        if config.transport == "ssl_tls":
            return imaplib.IMAP4_SSL(
                config.host, config.port, timeout=20
            )
        client = imaplib.IMAP4(config.host, config.port, timeout=20)
        client.starttls()
        return client

    @staticmethod
    def _default_smtp_factory(config: EmailConnectionConfig) -> Any:
        if config.transport == "ssl_tls":
            return smtplib.SMTP_SSL(
                config.host, config.port, timeout=20
            )
        client = smtplib.SMTP(config.host, config.port, timeout=20)
        client.starttls()
        return client
