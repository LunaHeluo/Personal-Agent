import json
from pathlib import Path

import pytest

from starter_agent.domain.models import ModelResponse
from starter_agent.knowledge.service import KnowledgeApplicationService
from starter_agent.knowledge.store import SQLiteKnowledgeStore
from starter_agent.settings import AgentSettings


class CrossDocumentProvider:
    name = "cross-document"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages, model, tools, **kwargs):
        assert tools == []
        self.calls += 1
        if self.calls == 1:
            return ModelResponse(
                content="候选人的 Agent 经历符合岗位要求。",
                provider=self.name,
                model=model,
            )
        return ModelResponse(
            content=json.dumps(
                {
                    "status": "answered",
                    "answer": "候选人的 Agent 经历符合岗位要求。",
                    "claims": [
                        {
                            "text": "候选人的 Agent 经历符合岗位要求。",
                            "evidence_refs": [
                                {
                                    "evidence_id": "E1",
                                    "quote": "负责 AI Agent 平台",
                                },
                                {
                                    "evidence_id": "E2",
                                    "quote": (
                                        "需要大模型应用和 Agent "
                                        "平台开发经验"
                                    ),
                                },
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            provider=self.name,
            model=model,
        )


def _service() -> KnowledgeApplicationService:
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
    return KnowledgeApplicationService(settings, store)


def _upload_comparison_documents(
    service: KnowledgeApplicationService,
) -> None:
    base_id = service.default_knowledge_base_id
    service.upload(
        knowledge_base_id=base_id,
        filename="resume.md",
        content=(
            "# 个人简历\n\n"
            "## 项目经历\n\n"
            "负责 AI Agent 平台和大语言模型应用开发。"
        ).encode(),
        document_type="resume",
        confirmed_authorized=True,
    )
    service.upload(
        knowledge_base_id=base_id,
        filename="agent-jd.md",
        content=(
            "# AI Agent 工程师\n\n"
            "## 岗位要求\n\n"
            "需要大模型应用和 Agent 平台开发经验。"
        ).encode(),
        document_type="job_description",
        confirmed_authorized=True,
    )


def _upload_unlabelled_comparison_documents(
    service: KnowledgeApplicationService,
) -> None:
    base_id = service.default_knowledge_base_id
    service.upload(
        knowledge_base_id=base_id,
        filename="candidate-profile.md",
        content=(
            "# 教育背景\n\n"
            "## 科研项目\n\n"
            "使用 Python 完成实验数据分析与论文复现。"
        ).encode(),
        document_type="resume",
        confirmed_authorized=True,
    )
    service.upload(
        knowledge_base_id=base_id,
        filename="research-opening.md",
        content=(
            "# 科研助理\n\n"
            "## 任职条件\n\n"
            "使用 Python 开展实验数据分析与论文复现。"
        ).encode(),
        document_type="job_description",
        confirmed_authorized=True,
    )


def test_natural_chinese_comparison_retrieves_resume_and_job_description() -> None:
    service = _service()
    _upload_comparison_documents(service)

    matches = service.retrieve(
        service.default_knowledge_base_id,
        "我的简历匹配哪个岗位",
        top_k=5,
    )

    assert {match.document_type for match in matches} >= {
        "resume",
        "job_description",
    }
    assert [match.rank for match in matches] == list(
        range(1, len(matches) + 1)
    )
    assert {match.mapping_version for match in matches} == {"builtin-v1"}


def test_comparison_retrieves_typed_documents_without_generic_labels() -> None:
    service = _service()
    _upload_unlabelled_comparison_documents(service)

    matches = service.retrieve(
        service.default_knowledge_base_id,
        "我的简历匹配什么岗位",
        top_k=5,
    )

    assert {match.document_type for match in matches} >= {
        "resume",
        "job_description",
    }


def test_comparison_coverage_respects_filters_and_top_k() -> None:
    service = _service()
    _upload_comparison_documents(service)
    base_id = service.default_knowledge_base_id

    filtered = service.retrieve(
        base_id,
        "我的简历匹配哪个岗位",
        top_k=5,
        document_types=["resume"],
    )
    limited = service.retrieve(
        base_id,
        "我的简历匹配哪个岗位",
        top_k=1,
    )

    assert filtered
    assert {match.document_type for match in filtered} == {"resume"}
    assert len(limited) == 1


@pytest.mark.asyncio
async def test_cross_document_answer_validates_each_quote_and_keeps_legacy_fields() -> None:
    service = _service()
    _upload_comparison_documents(service)
    provider = CrossDocumentProvider()
    service.providers.get = lambda _name: provider

    answer = await service.answer(
        service.default_knowledge_base_id,
        "我的简历匹配哪个岗位",
    )

    claim = answer.claims[0].model_dump(mode="json")
    assert answer.status == "answered"
    assert [item.filename for item in answer.citations] == [
        "resume.md",
        "agent-jd.md",
    ]
    assert claim["evidence_ids"] == ["E1", "E2"]
    assert claim["quote"] == "负责 AI Agent 平台"
    assert claim["evidence_refs"] == [
        {"evidence_id": "E1", "quote": "负责 AI Agent 平台"},
        {
            "evidence_id": "E2",
            "quote": "需要大模型应用和 Agent 平台开发经验",
        },
    ]
    assert provider.calls == 2
    evidence_text_by_filename = {
        "resume.md": "负责 AI Agent 平台和大语言模型应用开发。",
        "agent-jd.md": "需要大模型应用和 Agent 平台开发经验。",
    }
    assert all(
        citation.quote
        in evidence_text_by_filename[citation.filename]
        for citation in answer.citations
    )
