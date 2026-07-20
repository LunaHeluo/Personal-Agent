from pathlib import Path
from unittest.mock import Mock

import pytest

from starter_agent.knowledge.models import KnowledgeScope
from starter_agent.knowledge.service import KnowledgeApplicationService
from starter_agent.knowledge.store import SQLiteKnowledgeStore
from starter_agent.settings import AgentSettings


@pytest.mark.asyncio
async def test_no_evidence_refuses_without_loading_provider() -> None:
    settings = AgentSettings.model_validate(
        {
            "providers": {"mock": {"type": "mock", "models": ["starter-mock"]}},
            "model": {"default_provider": "mock", "default_model": "starter-mock"},
            "project_root": Path.cwd(),
            "app": {"database_url": "sqlite+pysqlite:///:memory:"},
        }
    )
    service = KnowledgeApplicationService(
        settings,
        SQLiteKnowledgeStore("sqlite+pysqlite:///:memory:", Path.cwd()),
    )
    service.retriever.retrieve = Mock(return_value=[])
    service.providers.get = Mock(side_effect=AssertionError("provider must not load"))

    result = await service.answer(service.default_knowledge_base_id, "不存在的 HR 邮箱")

    assert result.status == "refused"
    assert result.refusal_reason == "no_evidence"
    assert "知识库中没有足够证据" in result.answer
    service.providers.get.assert_not_called()
