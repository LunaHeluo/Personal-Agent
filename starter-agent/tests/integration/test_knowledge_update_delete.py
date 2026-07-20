from uuid import uuid4

from fastapi.testclient import TestClient

from starter_agent.bootstrap import create_knowledge_service
from starter_agent.interfaces.api import create_api


def _upload(client: TestClient, base_id: str, marker: str) -> dict:
    response = client.post(
        f"/v1/knowledge-bases/{base_id}/documents",
        data={"document_type": "resume", "confirmed_authorized": "true"},
        files={
            "file": (
                f"resume-{marker}.md",
                f"# 经历\n\n旧项目 Old{marker}。".encode(),
                "text/markdown",
            )
        },
    )
    assert response.status_code == 202
    return response.json()


def test_update_requires_matching_active_fingerprint_and_invalidates_old_chunk() -> None:
    create_knowledge_service.cache_clear()
    marker = uuid4().hex[:8]
    with TestClient(create_api()) as client:
        base_id = client.get("/v1/knowledge-bases").json()["knowledge_bases"][0]["id"]
        uploaded = _upload(client, base_id, marker)
        document_url = (
            f"/v1/knowledge-bases/{base_id}/documents/{uploaded['document_id']}"
        )
        document = client.get(document_url).json()
        old_chunk = client.get(f"{document_url}/chunks").json()["chunks"][0]["id"]

        conflict = client.put(
            f"{document_url}/content",
            headers={"If-Match": "wrong"},
            data={"confirmed_authorized": "true"},
            files={"file": ("resume.md", b"# New\n\nSafe.", "text/markdown")},
        )
        updated = client.put(
            f"{document_url}/content",
            headers={"If-Match": document["content_sha256"]},
            data={"confirmed_authorized": "true"},
            files={
                "file": (
                    "resume.md",
                    f"# 经历\n\n新项目 New{marker}。".encode(),
                    "text/markdown",
                )
            },
        )
        old_search = client.post(
            f"/v1/knowledge-bases/{base_id}/retrieve",
            json={"question": f"Old{marker}"},
        )
        new_search = client.post(
            f"/v1/knowledge-bases/{base_id}/retrieve",
            json={"question": f"New{marker}"},
        )
        old_citation = client.get(
            f"/v1/knowledge-bases/{base_id}/citations/{old_chunk}"
        )

    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "document_version_conflict"
    assert updated.status_code == 202
    assert old_search.json()["status"] == "no_evidence"
    assert new_search.json()["status"] == "ok"
    assert old_citation.status_code == 410


def test_delete_is_idempotent_and_removes_document_chunks_and_search() -> None:
    create_knowledge_service.cache_clear()
    marker = uuid4().hex[:8]
    with TestClient(create_api()) as client:
        base_id = client.get("/v1/knowledge-bases").json()["knowledge_bases"][0]["id"]
        uploaded = _upload(client, base_id, marker)
        url = f"/v1/knowledge-bases/{base_id}/documents/{uploaded['document_id']}"

        deleted = client.delete(url)
        repeated = client.delete(url)
        document = client.get(url)
        search = client.post(
            f"/v1/knowledge-bases/{base_id}/retrieve",
            json={"question": f"Old{marker}"},
        )

    assert deleted.status_code == 200
    assert repeated.status_code == 200
    assert document.status_code == 404
    assert search.json()["status"] == "no_evidence"
