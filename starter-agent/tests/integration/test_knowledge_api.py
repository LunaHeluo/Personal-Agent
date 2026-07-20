from uuid import uuid4

from fastapi.testclient import TestClient

from starter_agent.bootstrap import (
    create_application,
    create_knowledge_service,
    get_settings,
)
from starter_agent.interfaces.api import create_api


def reset_services() -> None:
    create_application.cache_clear()
    create_knowledge_service.cache_clear()
    get_settings.cache_clear()


def test_upload_lists_and_reads_knowledge_document() -> None:
    reset_services()
    unique_profile = f"# Resume\nSafe fictional profile {uuid4()}.".encode()
    with TestClient(create_api()) as client:
        bases = client.get("/v1/knowledge-bases")
        knowledge_base_id = bases.json()["knowledge_bases"][0]["id"]
        uploaded = client.post(
            f"/v1/knowledge-bases/{knowledge_base_id}/documents",
            data={
                "document_type": "resume",
                "confirmed_authorized": "true",
            },
            files={
                "file": (
                    "resume_demo.md",
                    unique_profile,
                    "text/markdown",
                )
            },
        )
        assert uploaded.status_code == 202
        body = uploaded.json()
        listed = client.get(
            f"/v1/knowledge-bases/{knowledge_base_id}/documents"
        )
        document = client.get(
            f"/v1/knowledge-bases/{knowledge_base_id}/documents/"
            f"{body['document_id']}"
        )
        job = client.get(
            f"/v1/knowledge-bases/{knowledge_base_id}/ingestion-jobs/"
            f"{body['job_id']}"
        )
        chunks = client.get(
            f"/v1/knowledge-bases/{knowledge_base_id}/documents/"
            f"{body['document_id']}/chunks"
        )

    assert body["status"] == "queued"
    assert body["stage"] == "upload"
    assert len(body["content_sha256"]) == 64
    assert listed.status_code == 200
    listed_document = next(
        item
        for item in listed.json()["documents"]
        if item["id"] == body["document_id"]
    )
    assert listed_document["filename"] == "resume_demo.md"
    assert listed_document["chunk_count"] >= 1
    assert document.status_code == 200
    assert job.status_code == 200
    assert job.json()["status"] == "succeeded"
    assert document.json()["status"] == "indexed"
    assert document.json()["chunk_count"] >= 1
    assert chunks.status_code == 200
    assert chunks.json()["chunks"][0]["source_ref"].endswith("#L2-L2")
    assert len(chunks.json()["chunks"][0]["preview"]) <= 400


def test_upload_api_exposes_safe_validation_error() -> None:
    reset_services()
    with TestClient(create_api()) as client:
        knowledge_base_id = client.get("/v1/knowledge-bases").json()[
            "knowledge_bases"
        ][0]["id"]
        response = client.post(
            f"/v1/knowledge-bases/{knowledge_base_id}/documents",
            data={
                "document_type": "resume",
                "confirmed_authorized": "false",
            },
            files={"file": ("resume.md", b"# Resume", "text/markdown")},
        )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == (
        "upload_authorization_required"
    )
    assert "# Resume" not in response.text
