from datetime import UTC, datetime, timedelta
from uuid import uuid4

from starter_agent.domain.models import Message
from starter_agent.infrastructure.session_store import SQLiteSessionStore


def _complete_candidate(title: str) -> dict[str, object]:
    return {
        "title": title,
        "company": "示例科技",
        "location": "北京",
        "source_url": f"https://careers.example.test/jobs/{title}",
        "responsibilities": ["负责智能体系统研发"],
        "requirements": ["熟悉 Python"],
        "analysis": [{"status": "matched", "requirement": "Python"}],
        "evidence_level": "complete",
    }


def test_replacing_pending_candidates_expires_previous_snapshot(tmp_path) -> None:
    store = SQLiteSessionStore("sqlite:///sessions.db", tmp_path)
    session_id = store.create_session()
    first = store.replace_pending_job_candidates(
        session_id=session_id,
        turn_id=uuid4(),
        candidates=[_complete_candidate("first")],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    second = store.replace_pending_job_candidates(
        session_id=session_id,
        turn_id=uuid4(),
        candidates=[_complete_candidate("second")],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    resolved = store.resolve_pending_job_candidate(session_id, ordinal=1)
    assert resolved is not None
    assert resolved.title == "second"
    assert first[0].candidate_id != second[0].candidate_id
    assert store.resolve_pending_job_candidate(
        session_id, candidate_id=first[0].candidate_id
    ) is None


def test_pending_candidate_is_scoped_to_session_and_expiry(tmp_path) -> None:
    store = SQLiteSessionStore("sqlite:///sessions.db", tmp_path)
    owner = store.create_session()
    other = store.create_session()
    expired = store.replace_pending_job_candidates(
        session_id=owner,
        turn_id=uuid4(),
        candidates=[_complete_candidate("expired")],
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    assert store.resolve_pending_job_candidate(
        owner, candidate_id=expired[0].candidate_id
    ) is None
    assert store.resolve_pending_job_candidate(
        other, candidate_id=expired[0].candidate_id
    ) is None


def test_pending_candidates_keep_visible_order_and_payload(tmp_path) -> None:
    store = SQLiteSessionStore("sqlite:///sessions.db", tmp_path)
    session_id = store.create_session()

    stored = store.replace_pending_job_candidates(
        session_id=session_id,
        turn_id=uuid4(),
        candidates=[_complete_candidate("first"), _complete_candidate("second")],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    assert [item.ordinal for item in stored] == [1, 2]
    assert [item.title for item in store.list_pending_job_candidates(session_id)] == [
        "first",
        "second",
    ]
    assert stored[0].payload["requirements"] == ["熟悉 Python"]


def test_session_pagination_and_clear_all_preserves_long_term_memory(tmp_path) -> None:
    store = SQLiteSessionStore("sqlite:///sessions.db", tmp_path)
    session_ids = []
    for index in range(5):
        session_id = store.create_session()
        session_ids.append(session_id)
        store.add_message(
            session_id,
            uuid4(),
            Message(role="user", content=f"conversation-{index}"),
        )

    store.create_memory(
        key="target_city",
        value="上海",
        category="preference",
        source_ref="user:memory-panel",
        source_type="user_confirmed",
        confidence=1.0,
        verified_by="user",
        expires_at=datetime.now(UTC) + timedelta(days=180),
        sensitivity="personal",
    )

    first_page = store.list_sessions(limit=2, offset=0)
    second_page = store.list_sessions(limit=2, offset=2)

    assert store.count_sessions() == 5
    assert len(first_page) == 2
    assert len(second_page) == 2
    assert {item.id for item in first_page}.isdisjoint(
        {item.id for item in second_page}
    )

    deleted = store.delete_all_sessions()

    assert deleted == 5
    assert store.count_sessions() == 0
    assert store.list_sessions() == []
    assert store.list_memories()[0].value == "上海"
