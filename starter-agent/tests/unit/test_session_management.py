from datetime import UTC, datetime, timedelta
from uuid import uuid4

from starter_agent.domain.models import Message
from starter_agent.infrastructure.session_store import SQLiteSessionStore


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
