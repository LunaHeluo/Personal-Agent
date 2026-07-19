from uuid import uuid4

import pytest

from starter_agent.knowledge.errors import KnowledgeError
from starter_agent.knowledge.models import KnowledgeScope
from starter_agent.knowledge.store import SQLiteKnowledgeStore


def test_store_persists_document_and_enforces_scope(tmp_path) -> None:
    database_url = "sqlite:///knowledge.db"
    owner = KnowledgeScope(user_id="user-a", project_id="project-a")
    other = KnowledgeScope(user_id="user-b", project_id="project-a")
    knowledge_base_id = uuid4()
    store = SQLiteKnowledgeStore(database_url, tmp_path)
    store.ensure_knowledge_base(
        owner, knowledge_base_id=knowledge_base_id, name="求职资料"
    )

    created = store.create_upload(
        owner,
        knowledge_base_id=knowledge_base_id,
        filename="resume.md",
        document_type="resume",
        source_text="# Resume\nSafe content",
        content_sha256="a" * 64,
    )

    reopened = SQLiteKnowledgeStore(database_url, tmp_path)
    assert reopened.get_document(owner, knowledge_base_id, created.document.id)
    assert (
        reopened.get_document(other, knowledge_base_id, created.document.id)
        is None
    )
    assert reopened.get_job(owner, knowledge_base_id, created.job.id)


def test_store_rejects_duplicate_content_in_same_scope(tmp_path) -> None:
    scope = KnowledgeScope(user_id="user-a", project_id="project-a")
    knowledge_base_id = uuid4()
    store = SQLiteKnowledgeStore("sqlite:///knowledge.db", tmp_path)
    store.ensure_knowledge_base(
        scope, knowledge_base_id=knowledge_base_id, name="求职资料"
    )
    values = {
        "knowledge_base_id": knowledge_base_id,
        "filename": "resume.md",
        "document_type": "resume",
        "source_text": "# Resume",
        "content_sha256": "a" * 64,
    }

    store.create_upload(scope, **values)
    with pytest.raises(KnowledgeError) as error:
        store.create_upload(scope, **values)

    assert error.value.code == "duplicate_document_content"
