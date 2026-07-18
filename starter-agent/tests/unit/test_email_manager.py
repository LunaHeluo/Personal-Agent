import json
from pathlib import Path
from uuid import uuid4

import pytest

from starter_agent.agent.token_counter import TokenCounter
from starter_agent.agent.tool_result_guard import ToolResultGuard
from starter_agent.domain.models import ToolResult
from starter_agent.settings import EmailProfileConfig, EmailToolConfig
from starter_agent.tools.email.errors import EmailError, EmailErrorCode
from starter_agent.tools.email.manager import EmailManager
from starter_agent.tools.email.models import EmailSearchQuery
from starter_agent.tools.email.store import SQLiteEmailStore


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def manager(tmp_path, *, body_max_chars: int = 12_000) -> EmailManager:
    config = EmailToolConfig(
        active_profile="mock",
        body_max_chars=body_max_chars,
        profiles={
            "mock": EmailProfileConfig(
                adapter="mock_fixture",
                fixture_root="tests/fixtures/email",
            ),
            "disabled": EmailProfileConfig(
                adapter="mock_fixture",
                fixture_root="tests/fixtures/email",
                enabled=False,
            ),
        },
    )
    return EmailManager(
        config=config,
        project_root=PROJECT_ROOT,
        store=SQLiteEmailStore("sqlite:///manager-email.db", tmp_path),
    )


async def test_manager_search_externalizes_refs_and_reads_message(
    tmp_path,
) -> None:
    email_manager = manager(tmp_path)
    session_id = str(uuid4())

    page = await email_manager.search(
        EmailSearchQuery(subject="Interview invitation"),
        session_id=session_id,
    )
    selected = page.messages[0]
    message = await email_manager.read(
        selected.message_ref,
        session_id=session_id,
    )

    assert selected.message_ref.startswith("email-message:")
    assert "fixture-message" not in selected.message_ref
    assert page.source_ref.startswith("email-source:")
    assert message.message_ref.startswith("email-message:")
    assert message.source_ref.startswith("email-source:")
    assert "Interview invitation" in message.headers.subject


async def test_manager_rejects_empty_search_and_unknown_profile(tmp_path) -> None:
    email_manager = manager(tmp_path)
    with pytest.raises(EmailError) as empty:
        await email_manager.search(
            EmailSearchQuery(),
            session_id=str(uuid4()),
        )
    with pytest.raises(EmailError) as unknown:
        await email_manager.search(
            EmailSearchQuery(subject="Interview"),
            session_id=str(uuid4()),
            profile="missing",
        )

    assert empty.value.code == EmailErrorCode.QUERY_INVALID
    assert unknown.value.code == EmailErrorCode.PROFILE_NOT_FOUND


async def test_manager_rejects_disabled_profile(tmp_path) -> None:
    with pytest.raises(EmailError) as error:
        await manager(tmp_path).search(
            EmailSearchQuery(subject="Interview"),
            session_id=str(uuid4()),
            profile="disabled",
        )

    assert error.value.code == EmailErrorCode.PROFILE_DISABLED


async def test_manager_long_body_has_completeness_and_source(tmp_path) -> None:
    email_manager = manager(tmp_path, body_max_chars=1_000)
    session_id = str(uuid4())
    page = await email_manager.search(
        EmailSearchQuery(subject="Long interview"),
        session_id=session_id,
    )

    message = await email_manager.read(
        page.messages[0].message_ref,
        session_id=session_id,
        max_body_chars=5_000,
    )

    assert len(message.body_text) == 1_000
    assert message.is_truncated is True
    assert message.has_more is True
    assert email_manager.resolve_source(
        message.source_ref, session_id=session_id
    ) == "fixture-source:msg-long-001"


async def test_manager_cursor_is_bound_to_query(tmp_path) -> None:
    email_manager = manager(tmp_path)
    session_id = str(uuid4())
    first = await email_manager.search(
        EmailSearchQuery(keywords=["interview"], limit=1),
        session_id=session_id,
    )

    assert first.next_cursor is not None
    with pytest.raises(EmailError) as error:
        await email_manager.search(
            EmailSearchQuery(
                keywords=["offer"], limit=1, cursor=first.next_cursor
            ),
            session_id=session_id,
        )
    assert error.value.code == EmailErrorCode.CURSOR_INVALID


async def test_runtime_guard_keeps_email_result_traceable(tmp_path) -> None:
    email_manager = manager(tmp_path)
    session_id = str(uuid4())
    page = await email_manager.search(
        EmailSearchQuery(keywords=["interview"], limit=10),
        session_id=session_id,
    )
    result = ToolResult(
        ok=True,
        data=page.model_dump(mode="json"),
        metadata={
            "is_truncated": page.is_truncated,
            "has_more": page.has_more,
            "source_ref": page.source_ref,
        },
    )
    guarded = ToolResultGuard(
        TokenCounter(safety_ratio=1.15), max_result_tokens=300
    ).guard(
        result.model_dump_json(),
        "email_search",
        "call-email-1",
        "tool:email_search:turn:call",
    )

    payload = json.loads(guarded.content)
    assert payload["metadata"]["is_truncated"] is True
    assert payload["metadata"]["raw_source_ref"].startswith("tool:")
    assert guarded.is_truncated is True
