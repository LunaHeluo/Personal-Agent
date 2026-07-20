from __future__ import annotations

import json
import re

from pydantic import BaseModel, ValidationError

from starter_agent.domain.models import Message
from starter_agent.knowledge.citations import assemble_citations
from starter_agent.knowledge.context import build_evidence_context
from starter_agent.knowledge.errors import KnowledgeError
from starter_agent.knowledge.models import (
    Evidence,
    GeneratedClaim,
    RagAnswer,
)
from starter_agent.providers.base import Provider


class _GeneratedPayload(BaseModel):
    status: str
    answer: str
    claims: list[GeneratedClaim]


_FENCED_JSON = re.compile(
    r"\A```(?:json)?[ \t]*\r?\n(?P<body>.*)\r?\n```[ \t]*\Z",
    re.IGNORECASE | re.DOTALL,
)


def _normalize_json_envelope(content: str) -> str:
    stripped = content.strip()
    match = _FENCED_JSON.fullmatch(stripped)
    return match.group("body").strip() if match else stripped


class RagGenerator:
    def __init__(self, provider: Provider, model: str) -> None:
        self.provider = provider
        self.model = model

    async def generate(
        self, question: str, evidence: list[Evidence]
    ) -> RagAnswer:
        if not evidence:
            return RagAnswer(
                status="refused",
                answer="知识库中没有足够证据回答该问题。",
                refusal_reason="no_evidence",
            )
        messages = [
            Message(
                role="system",
                content=(
                    "你是受证据约束的问答器。输出 JSON："
                    "status、answer、claims；每个 claim 包含 text、"
                    "evidence_ids、quote。不得使用资料外事实。"
                ),
            ),
            Message(
                role="user",
                content=f"问题：{question}\n\n{build_evidence_context(evidence)}",
            ),
        ]
        response = await self.provider.complete(
            messages, self.model, tools=[]
        )
        try:
            payload = _GeneratedPayload.model_validate_json(
                _normalize_json_envelope(response.content or "")
            )
            status = payload.status
            if status not in {"answered", "refused", "conflict"}:
                raise ValueError(status)
            citations = assemble_citations(payload.claims, evidence)
            return RagAnswer(
                status=status,
                answer=payload.answer,
                claims=payload.claims,
                citations=citations,
            )
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            raise KnowledgeError("generation_invalid_output") from exc
