import pytest

from starter_agent.job_research.jd import (
    JobDescriptionIngestionError,
    JobDescriptionIngestionService,
    JobDescriptionNormalizer,
)
from starter_agent.knowledge.service import KnowledgeApplicationService
from starter_agent.knowledge.store import SQLiteKnowledgeStore


def _normalized(source_url: str = "https://jobs.example/roles/42"):
    return JobDescriptionNormalizer().normalize(
        {
            "title": "Agent Engineer",
            "company": "Example Corp",
            "location": "Shanghai",
            "responsibilities": ["Build agent systems"],
            "requirements": ["Python experience"],
            "final_url": source_url,
            "content_sha256": "a" * 64,
        },
        call_id="call-42",
        snapshot_id="snapshot-7",
        schema_hash="b" * 64,
    )


def _service(settings) -> tuple[JobDescriptionIngestionService, KnowledgeApplicationService]:
    knowledge = KnowledgeApplicationService(
        settings,
        SQLiteKnowledgeStore(settings.app.database_url, settings.project_root),
    )
    return JobDescriptionIngestionService(knowledge), knowledge


def test_ingestion_requires_confirmation_before_any_knowledge_write(settings) -> None:
    ingestion, knowledge = _service(settings)

    with pytest.raises(JobDescriptionIngestionError, match="confirmation_required"):
        ingestion.ingest(_normalized(), confirmed=False)

    assert knowledge.list_documents(knowledge.default_knowledge_base_id) == []


def test_confirmed_complete_jd_uses_real_knowledge_upload_service(settings) -> None:
    ingestion, knowledge = _service(settings)

    receipt = ingestion.ingest(_normalized(), confirmed=True)
    stored = knowledge.get_document(
        knowledge.default_knowledge_base_id, receipt.document_id
    )

    assert stored.document_type == "job_description"
    assert stored.status == "indexed"
    assert receipt.trace.call_id == "call-42"
    assert receipt.trace.snapshot_id == "snapshot-7"
    assert receipt.trace.schema_hash == "b" * 64
    assert receipt.trace.source_url == "https://jobs.example/roles/42"
    assert receipt.trace.ingestion_job_id == receipt.job_id


def test_incomplete_jd_is_rejected_even_after_confirmation(settings) -> None:
    ingestion, knowledge = _service(settings)
    incomplete = JobDescriptionNormalizer().normalize(
        {"title": "Agent Engineer", "final_url": "https://jobs.example/42"}
    )

    with pytest.raises(JobDescriptionIngestionError, match="incomplete_job_description"):
        ingestion.ingest(incomplete, confirmed=True)

    assert knowledge.list_documents(knowledge.default_knowledge_base_id) == []


def test_duplicate_source_url_returns_conflict_instead_of_copying(settings) -> None:
    ingestion, knowledge = _service(settings)
    ingestion.ingest(_normalized(), confirmed=True)

    with pytest.raises(JobDescriptionIngestionError, match="duplicate_source_url"):
        ingestion.ingest(_normalized(), confirmed=True)

    assert len(knowledge.list_documents(knowledge.default_knowledge_base_id)) == 1
