from uuid import uuid4

from fastapi.testclient import TestClient

import starter_agent.interfaces.api as api_module
from starter_agent.domain.models import ChatResult
from starter_agent.knowledge.models import RagAnswer


class FakeKnowledge:
    async def answer(self, knowledge_base_id, question, **kwargs):
        return RagAnswer(
            status="refused",
            answer="知识库中没有足够证据回答该问题。",
            refusal_reason="no_evidence",
        )


class FakeApplication:
    def __init__(self):
        self.called = False

    async def chat(self, **kwargs):
        self.called = True
        return ChatResult(
            session_id=uuid4(),
            turn_id=uuid4(),
            content="normal chat",
            provider="mock",
            model="starter-mock",
        )

    async def wait_for_background_tasks(self):
        return None


def test_chat_required_routes_to_knowledge_and_off_preserves_chat(monkeypatch) -> None:
    app_service = FakeApplication()
    monkeypatch.setattr(api_module, "create_knowledge_service", lambda: FakeKnowledge())
    monkeypatch.setattr(api_module, "create_application", lambda: app_service)
    base_id = uuid4()

    with TestClient(api_module.create_api()) as client:
        required = client.post(
            "/v1/chat",
            json={
                "message": "HR 邮箱是什么？",
                "knowledge_mode": "required",
                "knowledge_base_id": str(base_id),
            },
        )
        assert app_service.called is False
        off = client.post(
            "/v1/chat",
            json={"message": "hello", "knowledge_mode": "off"},
        )

    assert required.status_code == 200
    assert required.json()["knowledge_mode"] == "required"
    assert required.json()["refusal_reason"] == "no_evidence"
    assert off.json()["content"] == "normal chat"
    assert app_service.called is True
