from pathlib import Path
from uuid import uuid4

import pytest

from starter_agent.knowledge.ingestion import KnowledgeIngestionPipeline
from starter_agent.knowledge.models import KnowledgeScope
from starter_agent.knowledge.store import SQLiteKnowledgeStore


def test_recovery_marks_interrupted_jobs_and_returns_queued_work() -> None:
    scope = KnowledgeScope(user_id="u", project_id="p")
    base_id = uuid4()
    store = SQLiteKnowledgeStore("sqlite+pysqlite:///:memory:", Path.cwd())
    store.ensure_knowledge_base(scope, knowledge_base_id=base_id, name="KB")
    upload = store.create_upload(
        scope,
        knowledge_base_id=base_id,
        filename="resume.md",
        document_type="resume",
        source_text="# Resume\n\nSafe.",
        content_sha256="d" * 64,
    )
    store.mark_job_running(scope, upload.job.id, stage="parse")

    queued = store.recover_ingestion_jobs(scope)
    job = store.get_job(scope, base_id, upload.job.id)

    assert queued == []
    assert job.status == "failed"
    assert job.error_code == "ingestion_interrupted"


def test_recovery_returns_queued_upload_bundle() -> None:
    scope = KnowledgeScope(user_id="u", project_id="p")
    base_id = uuid4()
    store = SQLiteKnowledgeStore("sqlite+pysqlite:///:memory:", Path.cwd())
    store.ensure_knowledge_base(scope, knowledge_base_id=base_id, name="KB")
    upload = store.create_upload(
        scope,
        knowledge_base_id=base_id,
        filename="resume.md",
        document_type="resume",
        source_text="# Resume\n\nSafe queued.",
        content_sha256="e" * 64,
    )

    queued = store.recover_ingestion_jobs(scope)

    assert [item.job.id for item in queued] == [upload.job.id]


def test_pipeline_leaves_abruptly_interrupted_job_recoverable() -> None:
    scope = KnowledgeScope(user_id="u", project_id="p")
    base_id = uuid4()
    store = SQLiteKnowledgeStore("sqlite+pysqlite:///:memory:", Path.cwd())
    store.ensure_knowledge_base(scope, knowledge_base_id=base_id, name="KB")
    upload = store.create_upload(
        scope,
        knowledge_base_id=base_id,
        filename="resume.md",
        document_type="resume",
        source_text="# Resume\n\nSafe.",
        content_sha256="f" * 64,
    )
    pipeline = KnowledgeIngestionPipeline(
        store,
        target_chars=900,
        overlap_chars=120,
    )
    pipeline.parser.parse = lambda _text: (_ for _ in ()).throw(
        KeyboardInterrupt()
    )

    with pytest.raises(KeyboardInterrupt):
        pipeline.run(scope, upload)

    assert store.get_job(scope, base_id, upload.job.id).status == "running"
    store.recover_ingestion_jobs(scope)
    recovered = store.get_job(scope, base_id, upload.job.id)
    assert recovered.status == "failed"
    assert recovered.error_code == "ingestion_interrupted"


def test_pipeline_rejects_chunk_capacity_without_activating_chunks() -> None:
    scope = KnowledgeScope(user_id="u", project_id="p")
    base_id = uuid4()
    store = SQLiteKnowledgeStore("sqlite+pysqlite:///:memory:", Path.cwd())
    store.ensure_knowledge_base(scope, knowledge_base_id=base_id, name="KB")
    upload = store.create_upload(
        scope,
        knowledge_base_id=base_id,
        filename="resume.md",
        document_type="resume",
        source_text="# First\n\nSafe first.\n\n# Second\n\nSafe second.",
        content_sha256="a" * 64,
    )
    pipeline = KnowledgeIngestionPipeline(
        store,
        target_chars=900,
        overlap_chars=120,
        max_chunks=1,
    )

    pipeline.run(scope, upload)

    job = store.get_job(scope, base_id, upload.job.id)
    document = store.get_document(scope, base_id, upload.document.id)
    assert job.status == "failed"
    assert job.error_code == "knowledge_chunk_capacity_exceeded"
    assert document.status == "failed"
    assert store.count_active_chunks(scope, base_id) == 0
