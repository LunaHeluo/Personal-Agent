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
                    "你是受证据约束的问答器。只输出一个 JSON 对象，"
                    "不要输出解释、前言或其他自由文本。JSON 必须包含 "
                    "status、answer、claims。status 只能是 "
                    '"answered"、"refused"、"conflict"，不要输出 '
                    '"success" 或其他状态。每个 claim 必须包含 text 和 '
                    '"evidence_refs"；evidence_refs 中每个引用项只对应一个 '
                    'Evidence，并包含 "evidence_id" 与 "quote"。'
                    "evidence_id 只能使用下方证据中给出的 ID，quote 必须是"
                    "该 Evidence 中的非空连续原文，不得改写。多个 Evidence "
                    "必须分别提供各自 quote。不得输出文件名、版本、行号或 "
                    "Chunk ID，不得使用资料外事实。"
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
