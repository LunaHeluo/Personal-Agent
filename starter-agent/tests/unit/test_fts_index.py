from uuid import uuid4

from starter_agent.knowledge.chunker import KnowledgeChunker
from starter_agent.knowledge.index import SQLiteFtsIndex
from starter_agent.knowledge.models import KnowledgeScope
from starter_agent.knowledge.parser import MarkdownParser
from starter_agent.knowledge.store import SQLiteKnowledgeStore


def test_fts_index_returns_scope_filtered_bm25_matches(tmp_path) -> None:
    store = SQLiteKnowledgeStore("sqlite:///knowledge.db", tmp_path)
    scope = KnowledgeScope(user_id="u", project_id="p")
    base_id = uuid4()
    store.ensure_knowledge_base(scope, knowledge_base_id=base_id, name="KB")
    upload = store.create_upload(
        scope,
        knowledge_base_id=base_id,
        filename="job.md",
        document_type="job_description",
        source_text="# JD\n\n需要 Python 和知识库研发经验。",
        content_sha256="c" * 64,
    )
    chunks = KnowledgeChunker().chunk(
        parsed=MarkdownParser().parse(upload.version.source_text),
        scope=scope,
        knowledge_base_id=base_id,
        document_id=upload.document.id,
        version_id=upload.version.id,
        version=1,
        filename="job.md",
    )
    store.complete_chunking(scope, upload, chunks)

    matches = SQLiteFtsIndex(store.engine).search(
        scope, base_id, '"知识库"', limit=5
    )

    assert len(matches) == 1
    assert matches[0][0] == chunks[0].id
    assert isinstance(matches[0][1], float)
