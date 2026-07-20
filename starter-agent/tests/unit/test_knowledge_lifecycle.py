from sqlalchemy import text

from starter_agent.knowledge.store import SQLiteKnowledgeStore


def test_sqlite_store_enables_foreign_keys_and_secure_delete(tmp_path) -> None:
    store = SQLiteKnowledgeStore("sqlite:///knowledge.db", tmp_path)

    with store.engine.connect() as connection:
        foreign_keys = connection.execute(text("PRAGMA foreign_keys")).scalar()
        secure_delete = connection.execute(text("PRAGMA secure_delete")).scalar()

    assert foreign_keys == 1
    assert secure_delete == 1
