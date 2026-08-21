from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from starter_agent.domain.models import ToolResult
from starter_agent.tools.base import Tool, ToolContext
from starter_agent.tools.email.errors import EmailError, EmailErrorCode
from starter_agent.tools.email.manager import EmailManager
from starter_agent.tools.email.models import DraftCreateRequest, EmailSearchQuery


def _failure(error: EmailError) -> ToolResult:
    return ToolResult(
        ok=False,
        error_code=error.code.value,
        display=error.display,
        retryable=error.retryable,
        metadata=error.metadata,
    )


def _validation_failure() -> ToolResult:
    return ToolResult(
        ok=False,
        error_code=EmailErrorCode.INVALID_ARGUMENTS.value,
        display="邮件工具参数不正确",
    )


class EmailTool(Tool):
    def __init__(self, manager: EmailManager) -> None:
        self.manager = manager


class EmailSearchTool(EmailTool):
    name = "email_search"
    description = (
        "Search an authorized mailbox for recruiting or interview messages "
        "without changing read state. Returns traceable, possibly truncated "
        "summaries; use email_read with a returned message_ref for details."
    )
    risk_level = "read"
    input_schema = {
        "type": "object",
        "properties": {
            "profile": {"type": "string", "maxLength": 80},
            "sender": {"type": "string", "maxLength": 320},
            "recipient": {"type": "string", "maxLength": 320},
            "subject": {"type": "string", "maxLength": 300},
            "keywords": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 100},
                "maxItems": 10,
            },
            "date_from": {"type": "string", "format": "date-time"},
            "date_to": {"type": "string", "format": "date-time"},
            "unread_only": {"type": "boolean", "default": False},
            "mailbox": {
                "type": "string",
                "minLength": 1,
                "maxLength": 120,
                "default": "INBOX",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "default": 10,
            },
            "cursor": {"type": "string", "maxLength": 500},
        },
        "additionalProperties": False,
    }

    async def execute(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        values = dict(arguments)
        profile = values.pop("profile", None)
        try:
            query = EmailSearchQuery.model_validate(values)
            page = await self.manager.search(
                query,
                session_id=str(context.session_id),
                profile=str(profile) if profile else None,
            )
        except ValidationError:
            return _validation_failure()
        except EmailError as error:
            return _failure(error)
        return ToolResult(
            ok=True,
            data=page.model_dump(mode="json"),
            display=f"找到 {len(page.messages)} 封候选邮件",
            metadata={
                "profile": page.profile,
                "result_count": len(page.messages),
                "is_truncated": page.is_truncated,
                "has_more": page.has_more,
                "source_ref": page.source_ref,
            },
        )


class EmailReadTool(EmailTool):
    name = "email_read"
    description = (
        "Read one email by the opaque message_ref returned by email_search. "
        "Uses no-side-effect PEEK semantics and returns sanitized plain text, "
        "thread summaries, attachments metadata, and completeness fields."
    )
    risk_level = "read"
    input_schema = {
        "type": "object",
        "properties": {
            "profile": {"type": "string", "maxLength": 80},
            "message_ref": {
                "type": "string",
                "minLength": 8,
                "maxLength": 500,
            },
            "include_thread": {"type": "boolean", "default": True},
            "thread_limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "default": 10,
            },
            "max_body_chars": {
                "type": "integer",
                "minimum": 1000,
                "maximum": 50000,
                "default": 12000,
            },
        },
        "required": ["message_ref"],
        "additionalProperties": False,
    }

    async def execute(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        try:
            message_ref = str(arguments["message_ref"])
            message = await self.manager.read(
                message_ref,
                session_id=str(context.session_id),
                profile=(
                    str(arguments["profile"])
                    if arguments.get("profile")
                    else None
                ),
                include_thread=arguments.get("include_thread", True) is True,
                thread_limit=int(arguments.get("thread_limit", 10)),
                max_body_chars=int(arguments.get("max_body_chars", 12_000)),
            )
        except (KeyError, TypeError, ValueError):
            return _validation_failure()
        except EmailError as error:
            return _failure(error)
        return ToolResult(
            ok=True,
            data={"message": message.model_dump(mode="json")},
            display="已读取邮件详情；邮件已读状态未改变",
            metadata={
                "profile": arguments.get(
                    "profile", self.manager.config.active_profile
                ),
                "is_truncated": message.is_truncated,
                "has_more": message.has_more,
                "source_ref": message.source_ref,
            },
        )


class EmailCreateDraftTool(EmailTool):
    name = "email_create_draft"
    description = (
        "Create a draft after deterministic recipient, placeholder, reply, and "
        "attachment checks. Omit storage_scope to use the safe profile default: "
        "mock profiles use mock drafts and real IMAP/SMTP profiles use local "
        "drafts. This tool never sends email; its result always states sent=false."
    )
    risk_level = "write"
    input_schema = {
        "type": "object",
        "properties": {
            "profile": {"type": "string", "maxLength": 80},
            "storage_scope": {
                "type": "string",
                "enum": ["local", "mailbox", "mock"],
            },
            "to": {
                "type": "array",
                "items": {"type": "string", "format": "email"},
                "minItems": 1,
                "maxItems": 10,
            },
            "cc": {
                "type": "array",
                "items": {"type": "string", "format": "email"},
                "maxItems": 10,
            },
            "bcc": {
                "type": "array",
                "items": {"type": "string", "format": "email"},
                "maxItems": 10,
            },
            "subject": {
                "type": "string",
                "minLength": 1,
                "maxLength": 300,
            },
            "body_text": {
                "type": "string",
                "minLength": 1,
                "maxLength": 50000,
            },
            "in_reply_to": {"type": "string", "maxLength": 500},
            "attachment_refs": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 500},
                "maxItems": 10,
            },
            "evidence_source_refs": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 500},
                "maxItems": 30,
            },
            "idempotency_key": {
                "type": "string",
                "minLength": 16,
                "maxLength": 200,
            },
        },
        "required": [
            "to",
            "subject",
            "body_text",
            "idempotency_key",
        ],
        "additionalProperties": False,
    }

    async def execute(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        values = dict(arguments)
        try:
            selected, configured = self.manager.resolve_profile(
                str(values["profile"]) if values.get("profile") else None
            )
            values["profile"] = selected
            if configured.adapter == "mock_fixture":
                values["storage_scope"] = "mock"
            elif values.get("storage_scope") in {None, "mock"}:
                values["storage_scope"] = "local"
            request = DraftCreateRequest.model_validate(values)
            draft = await self.manager.create_draft(
                request, session_id=str(context.session_id)
            )
        except ValidationError:
            return _validation_failure()
        except EmailError as error:
            return _failure(error)
        return ToolResult(
            ok=True,
            data={
                "draft_id": draft.draft_id,
                "profile": draft.profile,
                "storage_scope": draft.storage_scope,
                "status": "draft_only",
                "sent": False,
                "external_delivery": False,
                "recipient_count": (
                    len(draft.to) + len(draft.cc) + len(draft.bcc)
                ),
                "subject": draft.subject,
                "content_sha256": draft.content_sha256,
                "attachment_sha256s": [
                    item.sha256 for item in draft.attachments
                ],
                "created_at": draft.created_at.isoformat(),
                "requires_approval_to_send": True,
            },
            display="草稿已创建，邮件尚未发送",
            metadata={
                "profile": draft.profile,
                "draft_id": draft.draft_id,
                "content_sha256": draft.content_sha256,
                "sent": False,
            },
        )


class EmailSendTool(EmailTool):
    name = "email_send"
    description = (
        "Send one existing immutable draft only after a trusted, server-side "
        "approval is bound to its recipients, content, and attachments. "
        "A model-provided boolean cannot approve sending."
    )
    risk_level = "external"
    input_schema = {
        "type": "object",
        "properties": {
            "profile": {"type": "string", "maxLength": 80},
            "draft_id": {
                "type": "string",
                "minLength": 8,
                "maxLength": 500,
            },
            "expected_content_sha256": {
                "type": "string",
                "pattern": "^[a-f0-9]{64}$",
            },
            "approval_id": {
                "type": "string",
                "minLength": 8,
                "maxLength": 500,
            },
            "idempotency_key": {
                "type": "string",
                "minLength": 16,
                "maxLength": 200,
            },
        },
        "required": [
            "draft_id",
            "expected_content_sha256",
            "approval_id",
            "idempotency_key",
        ],
        "additionalProperties": False,
    }

    async def execute(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        try:
            required = (
                "draft_id",
                "expected_content_sha256",
                "approval_id",
                "idempotency_key",
            )
            if any(not isinstance(arguments.get(name), str) for name in required):
                return _validation_failure()
            receipt = await self.manager.send(
                draft_id=str(arguments["draft_id"]),
                expected_content_sha256=str(
                    arguments["expected_content_sha256"]
                ),
                approval_id=str(arguments["approval_id"]),
                idempotency_key=str(arguments["idempotency_key"]),
                session_id=str(context.session_id),
                profile=(
                    str(arguments["profile"])
                    if arguments.get("profile")
                    else None
                ),
            )
        except EmailError as error:
            return _failure(error)
        return ToolResult(
            ok=True,
            data=receipt.model_dump(mode="json"),
            display=(
                "Mock 发送流程已完成；没有邮件对外发送"
                if not receipt.external_delivery
                else "邮件已由 SMTP provider 确认发送"
            ),
            metadata={
                "profile": arguments.get(
                    "profile", self.manager.config.active_profile
                ),
                "delivery_mode": receipt.delivery_mode,
                "external_delivery": receipt.external_delivery,
                "status": receipt.status,
                "source_ref": receipt.source_ref,
            },
        )
