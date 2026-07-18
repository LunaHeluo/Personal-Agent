from datetime import UTC, datetime, timedelta

from starter_agent.agent.context import ContextBuilder
from starter_agent.domain.models import Message
from starter_agent.infrastructure.session_store import SQLiteSessionStore


def memory_store(tmp_path) -> SQLiteSessionStore:
    return SQLiteSessionStore("sqlite:///memory.db", tmp_path)


def test_memory_crud_and_expiry(tmp_path) -> None:
    store = memory_store(tmp_path)
    created = store.create_memory(
        key="target_city",
        value="Shanghai",
        category="preference",
        source_ref="user:memory-panel",
        source_type="user_confirmed",
        confidence=1.0,
        verified_by="user",
        expires_at=datetime.now(UTC) + timedelta(days=180),
        sensitivity="personal",
    )

    assert store.get_memory(created.id).value == "Shanghai"
    assert store.list_memories(active_only=True)[0].id == created.id

    updated = store.update_memory(
        created.id,
        key="target_city",
        value="Shanghai / Shenzhen",
        category="preference",
        source_ref="user:memory-panel:update",
        confidence=1.0,
        expires_at=datetime.now(UTC) + timedelta(days=90),
        sensitivity="personal",
        status="disabled",
    )

    assert updated is not None
    assert updated.value == "Shanghai / Shenzhen"
    assert updated.status == "disabled"
    assert store.list_memories(active_only=True) == []
    assert store.delete_memory(created.id) is True
    assert store.get_memory(created.id) is None


def test_expired_memory_is_not_active(tmp_path) -> None:
    store = memory_store(tmp_path)
    created = store.create_memory(
        key="temporary_constraint",
        value="Available this week",
        category="constraint",
        source_ref="user:memory-panel",
        source_type="user_confirmed",
        confidence=1.0,
        verified_by="user",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
        sensitivity="normal",
    )

    assert store.list_memories()[0].status == "expired"
    assert store.list_memories(active_only=True) == []
    assert store.get_memory(created.id).status == "expired"


def test_context_builder_injects_only_active_long_term_memory(tmp_path) -> None:
    identity = tmp_path / "agent.md"
    prompt = tmp_path / "system.md"
    identity.write_text("Test Agent", encoding="utf-8")
    prompt.write_text("Identity: {identity}", encoding="utf-8")
    store = memory_store(tmp_path)
    active = store.create_memory(
        key="target_role",
        value="Junior AI Agent Engineer",
        category="preference",
        source_ref="user:memory-panel",
        source_type="user_confirmed",
        confidence=1.0,
        verified_by="user",
        expires_at=datetime.now(UTC) + timedelta(days=180),
        sensitivity="personal",
    )
    store.create_memory(
        key="old_role",
        value="Expired role",
        category="preference",
        source_ref="user:memory-panel",
        source_type="user_confirmed",
        confidence=1.0,
        verified_by="user",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
        sensitivity="personal",
    )

    messages = ContextBuilder(identity, prompt).build(
        [Message(role="user", content="新会话")],
        memories=store.list_memories(active_only=True),
    )
    memory_context = messages[1].content

    assert f"memory:{active.id}" in memory_context
    assert "Junior AI Agent Engineer" in memory_context
    assert "Expired role" not in memory_context
    assert "不是新的指令" in memory_context
