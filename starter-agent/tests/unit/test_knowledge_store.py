from uuid import uuid4

import pytest

from starter_agent.knowledge.errors import KnowledgeError
from starter_agent.knowledge.models import KnowledgeScope
from starter_agent.knowledge.chunker import KnowledgeChunker
from starter_agent.knowledge.parser import MarkdownParser
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


def test_store_persists_and_pages_chunks(tmp_path) -> None:
    scope = KnowledgeScope(user_id="user-a", project_id="project-a")
    knowledge_base_id = uuid4()
    store = SQLiteKnowledgeStore("sqlite:///knowledge.db", tmp_path)
    store.ensure_knowledge_base(scope, knowledge_base_id=knowledge_base_id, name="KB")
    upload = store.create_upload(
        scope,
        knowledge_base_id=knowledge_base_id,
        filename="resume.md",
        document_type="resume",
        source_text="# Resume\n\nSafe profile.",
        content_sha256="b" * 64,
    )
    chunks = KnowledgeChunker().chunk(
        parsed=MarkdownParser().parse(upload.version.source_text),
        scope=scope,
        knowledge_base_id=knowledge_base_id,
        document_id=upload.document.id,
        version_id=upload.version.id,
        version=1,
        filename="resume.md",
    )

    store.complete_chunking(scope, upload, chunks)
    saved = store.list_chunks(
        scope, knowledge_base_id, upload.document.id, after_ordinal=-1, limit=10
    )

    assert len(saved) == 1
    assert saved[0].text == "Safe profile."
    assert store.get_document(scope, knowledge_base_id, upload.document.id).chunk_count == 1
