from pathlib import Path

from starter_agent.knowledge.service import KnowledgeApplicationService
from starter_agent.knowledge.store import SQLiteKnowledgeStore
from starter_agent.settings import AgentSettings


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
