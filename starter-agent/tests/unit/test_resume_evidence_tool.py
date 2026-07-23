from types import SimpleNamespace
from uuid import uuid4

from starter_agent.tools.base import ToolContext
from starter_agent.tools.builtin.knowledge import RetrieveResumeEvidenceTool


class _Knowledge:
    def __init__(self, matches):
        self.scope = SimpleNamespace(user_id="user-1", project_id="project-1")
        self.matches = matches
        self.calls = []

    def retrieve(self, knowledge_base_id, question, **filters):
        self.calls.append((knowledge_base_id, question, filters))
        return self.matches


def _context(knowledge_base_id=None, *, user_id="user-1", project_id="project-1"):
    return ToolContext(
        session_id=uuid4(),
        turn_id=uuid4(),
        user_id=user_id,
        project_id=project_id,
        knowledge_base_id=knowledge_base_id or uuid4(),
    )


async def test_resume_evidence_tool_uses_context_scope_and_fixed_resume_filter():
    chunk_id, document_id = uuid4(), uuid4()
    knowledge = _Knowledge(
        [
            SimpleNamespace(
                chunk_id=chunk_id,
                document_id=document_id,
                filename="resume.md",
                version=3,
                section_path=["Experience", "Agent Platform"],
                start_line=12,
                end_line=14,
                preview="Built a governed RAG platform.\nReduced retrieval latency by 30%.",
                source_ref="resume.md@v3#L12-L14",
                rank=1,
            )
        ]
    )
    tool = RetrieveResumeEvidenceTool(knowledge)
    context = _context()

    result = await tool.execute({"query": "RAG latency", "top_k": 4}, context)

    assert result.ok is True
    assert knowledge.calls == [
        (
            context.knowledge_base_id,
            "RAG latency",
            {"top_k": 4, "document_types": ["resume"]},
        )
    ]
    assert result.data["evidence"] == [
        {
            "chunk_id": str(chunk_id),
            "document_id": str(document_id),
            "filename": "resume.md",
            "version": 3,
            "section": "Experience > Agent Platform",
            "start_line": 12,
            "end_line": 14,
            "quote": "Built a governed RAG platform.\nReduced retrieval latency by 30%.",
            "source_ref": "resume.md@v3#L12-L14",
            "rank": 1,
        }
    ]


async def test_resume_evidence_tool_fails_closed_without_evidence_or_scope():
    knowledge = _Knowledge([])
    tool = RetrieveResumeEvidenceTool(knowledge)

    no_evidence = await tool.execute({"query": "Kubernetes"}, _context())
    wrong_scope = await tool.execute(
        {"query": "RAG"},
        _context(user_id="other-user"),
    )

    assert no_evidence.ok is False
    assert no_evidence.error_code == "no_evidence"
    assert no_evidence.data == {"evidence": []}
    assert wrong_scope.ok is False
    assert wrong_scope.error_code == "knowledge_scope_mismatch"
    assert len(knowledge.calls) == 1


def test_resume_evidence_schema_exposes_no_model_controlled_scope():
    schema = RetrieveResumeEvidenceTool.input_schema

    assert schema["required"] == ["query"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"query", "top_k"}
    assert schema["properties"]["top_k"]["maximum"] == 20
