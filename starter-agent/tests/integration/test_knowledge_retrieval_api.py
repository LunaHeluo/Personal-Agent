from uuid import uuid4

from fastapi.testclient import TestClient

from starter_agent.bootstrap import create_knowledge_service
from starter_agent.interfaces.api import create_api


def test_retrieval_api_returns_source_ref_and_no_evidence() -> None:
    create_knowledge_service.cache_clear()
    marker = uuid4().hex[:8]
    with TestClient(create_api()) as client:
        base_id = client.get("/v1/knowledge-bases").json()["knowledge_bases"][0]["id"]
        uploaded = client.post(
            f"/v1/knowledge-bases/{base_id}/documents",
            data={"document_type": "resume", "confirmed_authorized": "true"},
            files={
                "file": (
                    f"resume-{marker}.md",
                    f"# 技能\n\n熟练使用 Nebula{marker} 构建知识库。".encode(),
                    "text/markdown",
                )
            },
        )
        assert uploaded.status_code == 202
        found = client.post(
            f"/v1/knowledge-bases/{base_id}/retrieve",
            json={"question": f"Nebula{marker}", "top_k": 3},
        )
        missing = client.post(
            f"/v1/knowledge-bases/{base_id}/retrieve",
            json={"question": f"不存在的邮箱{marker}", "top_k": 3},
        )
        short = client.post(
            f"/v1/knowledge-bases/{base_id}/retrieve",
            json={"question": "熟练", "top_k": 3},
        )

    assert found.status_code == 200
    assert found.json()["status"] == "ok"
    assert found.json()["matches"][0]["source_ref"].startswith(
        f"resume-{marker}.md@v1"
    )
    assert missing.status_code == 200
    assert missing.json() == {"status": "no_evidence", "matches": []}
    assert short.json()["status"] == "ok"
