import json
from uuid import uuid4

import pytest

from starter_agent.job_research.jd import JobDescriptionIngestionError
from starter_agent.knowledge.service import KnowledgeApplicationService
from starter_agent.knowledge.store import SQLiteKnowledgeStore


def _enable_backend(application, settings) -> KnowledgeApplicationService:
    knowledge = KnowledgeApplicationService(
        settings,
        SQLiteKnowledgeStore(settings.app.database_url, settings.project_root),
    )
    application.configure_job_description_ingestion(knowledge)
    return knowledge


def _save_artifact(
    application,
    *,
    session_id,
    turn_id,
    call_id: str,
    source_url: str,
    source_hash: str,
) -> str:
    source_ref = f"tool:jobs:{turn_id}:{call_id}"
    application.store.save_tool_artifact(
        source_ref=source_ref,
        session_id=session_id,
        turn_id=turn_id,
        call_id=call_id,
        tool_name="fetch_job",
        content=json.dumps(
            {
                "ok": True,
                "data": {
                    "title": "Agent Engineer",
                    "company": "Example Corp",
                    "location": "Shanghai",
                    "responsibilities": ["Build agent systems"],
                    "requirements": ["Python experience"],
                    "final_url": source_url,
                    "content_sha256": source_hash,
                },
                "metadata": {"is_untrusted_external_content": True},
            }
        ),
        server_id="jobs",
        snapshot_id="snapshot-7",
        schema_hash="b" * 64,
        requested_url=source_url,
        final_url=source_url,
        source_content_sha256=source_hash,
        content_sha256="c" * 64,
        truncation_summary={"reason": "token_budget"},
    )
    return source_ref


def test_application_ingestion_requires_bound_persisted_single_use_approval(
    application, settings
) -> None:
    knowledge = _enable_backend(application, settings)
    session_id = application.store.create_session()
    turn_id = uuid4()
    source_ref = _save_artifact(
        application,
        session_id=session_id,
        turn_id=turn_id,
        call_id="call-42",
        source_url="https://jobs.example/roles/42",
        source_hash="a" * 64,
    )

    challenge = application.prepare_job_description_ingestion(
        source_ref=source_ref,
        principal="local-user",
        session_id=session_id,
    )
    assert challenge.status == "pending"
    assert challenge.call_id == "call-42"
    assert challenge.snapshot_id == "snapshot-7"
    assert challenge.schema_hash == "b" * 64
    assert challenge.gate_reason_code == "job_description_ingestion_confirmation_required"
    assert knowledge.list_documents(knowledge.default_knowledge_base_id) == []

    with pytest.raises(JobDescriptionIngestionError, match="confirmation_not_approved"):
        application.ingest_job_description(
            challenge.id, principal="local-user", session_id=session_id
        )
    with pytest.raises(JobDescriptionIngestionError, match="confirmation_binding_mismatch"):
        application.approve_job_description_ingestion(
            challenge.id, principal="attacker", session_id=session_id
        )

    application.approve_job_description_ingestion(
        challenge.id, principal="local-user", session_id=session_id
    )
    receipt = application.ingest_job_description(
        challenge.id, principal="local-user", session_id=session_id
    )
    stored = knowledge.get_document(
        knowledge.default_knowledge_base_id, receipt.document_id
    )
    assert stored.document_type == "job_description"
    assert receipt.trace.gate_reason_code == challenge.gate_reason_code
    assert receipt.trace.confirmation_id == challenge.id
    assert receipt.trace.call_id == "call-42"
    assert receipt.trace.artifact_ref == source_ref
    assert receipt.trace.ingestion_job_id == receipt.job_id

    with pytest.raises(JobDescriptionIngestionError, match="confirmation_consumed"):
        application.ingest_job_description(
            challenge.id, principal="local-user", session_id=session_id
        )


def test_same_source_hash_across_different_urls_returns_conflict(
    application, settings
) -> None:
    knowledge = _enable_backend(application, settings)
    session_id = application.store.create_session()
    source_hash = "d" * 64

    def ingest(call_id: str, url: str):
        source_ref = _save_artifact(
            application,
            session_id=session_id,
            turn_id=uuid4(),
            call_id=call_id,
            source_url=url,
            source_hash=source_hash,
        )
        challenge = application.prepare_job_description_ingestion(
            source_ref=source_ref,
            principal="local-user",
            session_id=session_id,
        )
        application.approve_job_description_ingestion(
            challenge.id, principal="local-user", session_id=session_id
        )
        return application.ingest_job_description(
            challenge.id, principal="local-user", session_id=session_id
        )

    ingest("call-one", "https://jobs.example/roles/one")
    with pytest.raises(JobDescriptionIngestionError, match="duplicate_source_hash"):
        ingest("call-two", "https://mirror.example/roles/one")

    assert len(knowledge.list_documents(knowledge.default_knowledge_base_id)) == 1
