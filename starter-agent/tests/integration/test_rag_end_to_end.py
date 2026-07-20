import json
from pathlib import Path

from fastapi.testclient import TestClient

import starter_agent.interfaces.api as api_module
from starter_agent.domain.models import ModelResponse
from starter_agent.knowledge.service import KnowledgeApplicationService
from starter_agent.knowledge.store import SQLiteKnowledgeStore
from starter_agent.settings import AgentSettings


FIXTURES = Path("tests/fixtures/knowledge")


class EvidenceStubProvider:
    name = "evidence-stub"

    async def complete(self, messages, model, tools, **kwargs):
        assert tools == []
        assert "Aurora 招聘知识库" in messages[-1].content
        return ModelResponse(
            content=json.dumps(
                {
                    "status": "answered",
                    "answer": "候选人负责过 Aurora 招聘知识库。",
                    "claims": [
                        {
                            "text": "候选人负责过 Aurora 招聘知识库。",
                            "evidence_ids": ["E1"],
                            "quote": "Aurora 招聘知识库",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            provider=self.name,
            model=model,
        )


class LifespanApplication:
    async def wait_for_background_tasks(self):
        return None


def test_safe_documents_answer_refuse_update_delete_and_restart(monkeypatch) -> None:
    settings = AgentSettings.model_validate(
        {
            "providers": {
                "mock": {"type": "mock", "models": ["starter-mock"]}
            },
            "model": {
                "default_provider": "mock",
                "default_model": "starter-mock",
            },
            "project_root": Path.cwd(),
            "app": {"database_url": "sqlite+pysqlite:///:memory:"},
        }
    )
    store = SQLiteKnowledgeStore("sqlite+pysqlite:///:memory:", Path.cwd())
    service = KnowledgeApplicationService(settings, store)
    service.providers.get = lambda _name: EvidenceStubProvider()
    current = {"service": service}
    monkeypatch.setattr(
        api_module, "create_knowledge_service", lambda: current["service"]
    )
    monkeypatch.setattr(
        api_module, "create_application", lambda: LifespanApplication()
    )

    with TestClient(api_module.create_api()) as client:
        base_id = client.get("/v1/knowledge-bases").json()[
            "knowledge_bases"
        ][0]["id"]
        uploads = []
        for filename, document_type in (
            ("resume_demo.md", "resume"),
            ("job_demo.md", "job_description"),
        ):
            response = client.post(
                f"/v1/knowledge-bases/{base_id}/documents",
                data={
                    "document_type": document_type,
                    "confirmed_authorized": "true",
                },
                files={
                    "file": (
                        filename,
                        (FIXTURES / filename).read_bytes(),
                        "text/markdown",
                    )
                },
            )
            assert response.status_code == 202
            uploads.append(response.json())

        documents = client.get(
            f"/v1/knowledge-bases/{base_id}/documents"
        ).json()["documents"]
        resume = next(
            item for item in documents if item["filename"] == "resume_demo.md"
        )
        chunks = client.get(
            f"/v1/knowledge-bases/{base_id}/documents/{resume['id']}/chunks"
        ).json()["chunks"]
        retrieval = client.post(
            f"/v1/knowledge-bases/{base_id}/retrieve",
            json={"question": "Aurora 招聘知识库"},
        ).json()
        answer = client.post(
            f"/v1/knowledge-bases/{base_id}/answer",
            json={"question": "是否负责过 Aurora 招聘知识库？"},
        ).json()
        refusal = client.post(
            f"/v1/knowledge-bases/{base_id}/answer",
            json={"question": "真实 HR 手机号是什么？"},
        ).json()

        project_chunk = next(
            item
            for item in chunks
            if item["section_path"][-1] == "项目经历"
        )
        old_chunk_id = project_chunk["id"]
        updated_text = (FIXTURES / "resume_demo.md").read_text(
            encoding="utf-8"
        ).replace("提升 18%", "提升 22%")
        updated = client.put(
            f"/v1/knowledge-bases/{base_id}/documents/{resume['id']}/content",
            headers={"If-Match": resume["content_sha256"]},
            data={"confirmed_authorized": "true"},
            files={
                "file": (
                    "resume_demo.md",
                    updated_text.encode(),
                    "text/markdown",
                )
            },
        )
        old_number = client.post(
            f"/v1/knowledge-bases/{base_id}/retrieve",
            json={"question": "提升 18%"},
        ).json()
        new_number = client.post(
            f"/v1/knowledge-bases/{base_id}/retrieve",
            json={"question": "提升 22%"},
        ).json()
        invalidated = client.get(
            f"/v1/knowledge-bases/{base_id}/citations/{old_chunk_id}"
        )
        deleted = client.delete(
            f"/v1/knowledge-bases/{base_id}/documents/{resume['id']}"
        )
        current["service"] = KnowledgeApplicationService(settings, store)
        after_restart = client.post(
            f"/v1/knowledge-bases/{base_id}/retrieve",
            json={"question": "Aurora 招聘知识库"},
        ).json()
        old_document = client.get(
            f"/v1/knowledge-bases/{base_id}/documents/{resume['id']}"
        )
        old_citation = client.get(
            f"/v1/knowledge-bases/{base_id}/citations/{old_chunk_id}"
        )

    assert len(documents) == 2
    assert resume["chunk_count"] > 0
    assert project_chunk["section_path"] == ["林澄的求职简历", "项目经历"]
    assert project_chunk["start_line"] <= project_chunk["end_line"]
    assert retrieval["status"] == "ok"
    assert len(retrieval["matches"]) <= 5
    assert retrieval["matches"][0]["chunk_id"] == project_chunk["id"]
    assert retrieval["matches"][0]["document_id"] == resume["id"]
    assert retrieval["matches"][0]["filename"] == "resume_demo.md"
    assert retrieval["matches"][0]["version"] == 1
    assert retrieval["matches"][0]["section_path"][-1] == "项目经历"
    assert (
        retrieval["matches"][0]["start_line"]
        <= retrieval["matches"][0]["end_line"]
    )
    assert "Aurora 招聘知识库" in retrieval["matches"][0]["preview"]
    assert retrieval["matches"][0]["source_ref"].startswith(
        "resume_demo.md@v1#L"
    )
    assert answer["status"] == "answered"
    assert answer["citations"][0]["filename"] == "resume_demo.md"
    assert answer["citations"][0]["quote"] == "Aurora 招聘知识库"
    assert refusal["status"] == "refused"
    assert refusal["refusal_reason"] == "no_evidence"
    assert updated.status_code == 202
    assert old_number["status"] == "no_evidence"
    assert new_number["status"] == "ok"
    assert invalidated.status_code == 410
    assert deleted.status_code == 200
    assert after_restart["status"] == "no_evidence"
    assert old_document.status_code == 404
    assert old_citation.status_code == 404
