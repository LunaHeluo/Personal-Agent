from pathlib import Path
from uuid import uuid4

import pytest

from starter_agent.knowledge.errors import KnowledgeError
from starter_agent.knowledge.models import KnowledgeScope
from starter_agent.knowledge.store import SQLiteKnowledgeStore


def test_other_scope_cannot_update_delete_or_resolve_document() -> None:
    owner = KnowledgeScope(user_id="owner", project_id="p")
    other = KnowledgeScope(user_id="other", project_id="p")
    base_id = uuid4()
    store = SQLiteKnowledgeStore("sqlite+pysqlite:///:memory:", Path.cwd())
    store.ensure_knowledge_base(owner, knowledge_base_id=base_id, name="KB")
    upload = store.create_upload(
        owner,
        knowledge_base_id=base_id,
        filename="resume.md",
        document_type="resume",
        source_text="# Resume\n\nSafe.",
        content_sha256="f" * 64,
    )

    with pytest.raises(KnowledgeError) as error:
        store.create_update(
            other,
            knowledge_base_id=base_id,
            document_id=upload.document.id,
            expected_content_sha256="f" * 64,
            source_text="# Other",
            content_sha256="1" * 64,
        )

    assert error.value.code == "document_not_found"
    assert store.delete_document(other, base_id, upload.document.id) is False
    assert store.get_document(owner, base_id, upload.document.id) is not None
