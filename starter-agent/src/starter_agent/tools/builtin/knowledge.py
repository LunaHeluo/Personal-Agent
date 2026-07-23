from __future__ import annotations

from typing import Any, Protocol

from starter_agent.domain.models import ToolResult
from starter_agent.tools.base import Tool, ToolContext


class _KnowledgeService(Protocol):
    scope: Any

    def retrieve(
        self,
        knowledge_base_id,
        question: str,
        *,
        top_k: int,
        document_types: list[str],
    ) -> list[Any]: ...


class RetrieveResumeEvidenceTool(Tool):
    """Retrieve only resume chunks in the scope bound to the tool call."""

    name = "retrieve_resume_evidence"
    description = (
        "Retrieve quoted evidence from the current user's indexed resume. "
        "Returns no evidence instead of inferring missing experience."
    )
    risk_level = "read"
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "minLength": 1,
                "maxLength": 10_000,
            },
            "top_k": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "default": 6,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def __init__(self, knowledge: _KnowledgeService) -> None:
        self.knowledge = knowledge

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        parsed = self._validate_arguments(arguments)
        if isinstance(parsed, ToolResult):
            return parsed
        query, top_k = parsed
        if (
            context.knowledge_base_id is None
            or context.user_id is None
            or context.project_id is None
        ):
            return ToolResult(
                ok=False,
                display="当前调用没有可用的知识库作用域。",
                error_code="knowledge_scope_unavailable",
            )
        scope = self.knowledge.scope
        if (
            context.user_id != scope.user_id
            or context.project_id != scope.project_id
        ):
            return ToolResult(
                ok=False,
                display="知识库作用域与当前用户或项目不匹配。",
                error_code="knowledge_scope_mismatch",
            )
        matches = self.knowledge.retrieve(
            context.knowledge_base_id,
            query,
            top_k=top_k,
            document_types=["resume"],
        )
        evidence = [
            {
                "chunk_id": str(item.chunk_id),
                "document_id": str(item.document_id),
                "filename": item.filename,
                "version": item.version,
                "section": " > ".join(item.section_path) or None,
                "start_line": item.start_line,
                "end_line": item.end_line,
                "quote": item.preview,
                "source_ref": item.source_ref,
                "rank": item.rank,
            }
            for item in matches
            if isinstance(item.preview, str) and item.preview
        ]
        if not evidence:
            return ToolResult(
                ok=False,
                data={"evidence": []},
                display="当前作用域的简历中没有找到可引用证据。",
                error_code="no_evidence",
                metadata={"document_type": "resume"},
            )
        return ToolResult(
            ok=True,
            data={"query": query, "evidence": evidence},
            display=f"找到 {len(evidence)} 条简历证据。",
            metadata={"document_type": "resume", "evidence_count": len(evidence)},
        )

    @staticmethod
    def _validate_arguments(
        arguments: dict[str, Any],
    ) -> tuple[str, int] | ToolResult:
        if set(arguments) - {"query", "top_k"}:
            return ToolResult(
                ok=False,
                display="简历证据检索参数不正确。",
                error_code="invalid_arguments",
            )
        query = arguments.get("query")
        top_k = arguments.get("top_k", 6)
        if (
            not isinstance(query, str)
            or not 1 <= len(query.strip()) <= 10_000
            or isinstance(top_k, bool)
            or not isinstance(top_k, int)
            or not 1 <= top_k <= 20
        ):
            return ToolResult(
                ok=False,
                display="简历证据检索参数不正确。",
                error_code="invalid_arguments",
            )
        return query.strip(), top_k
