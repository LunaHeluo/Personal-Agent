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


def _validate_payload(content: str) -> _GeneratedPayload:
    payload = _GeneratedPayload.model_validate_json(
        _normalize_json_envelope(content)
    )
    if payload.status not in {"answered", "refused", "conflict"}:
        raise ValueError(payload.status)
    return payload


def _retry_reason(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        if any(
            item["type"] == "json_invalid"
            for item in exc.errors()
        ):
            return "invalid_json"
        return "schema_validation_failed"
    if isinstance(exc, json.JSONDecodeError):
        return "invalid_json"
    return "invalid_status"


_RETRY_EXAMPLE = {
    "status": "answered",
    "answer": "基于证据的回答",
    "claims": [
        {
            "text": "一项可由证据支持的结论",
            "evidence_refs": [
                {
                    "evidence_id": "E1",
                    "quote": "E1 中的逐字连续原文",
                },
                {
                    "evidence_id": "E2",
                    "quote": "E2 中的逐字连续原文",
                },
            ],
        }
    ],
}


def _build_messages(
    question: str,
    evidence: list[Evidence],
    *,
    retry_reason: str | None = None,
) -> list[Message]:
    retry_instruction = ""
    if retry_reason is not None:
        if retry_reason == "citation_validation_failed":
            retry_instruction = (
                "\n上一次输出未通过引用校验。请重新生成，不要解释错误。"
                "每个 evidence_id 必须来自下方 Evidence；每个 quote 必须"
                "从对应 Evidence 逐字复制非空连续原文，不得概括、改写或"
                "跨 Evidence 拼接。"
            )
        else:
            retry_instruction = (
                "\n上一次输出未通过结构校验，错误类型为 "
                f"{retry_reason}。请重新生成，不要解释错误。"
            )
        retry_instruction += (
            "\n只输出一个 JSON 对象，格式示例："
            f"\n{json.dumps(_RETRY_EXAMPLE, ensure_ascii=False)}"
            "\n不得同时输出旧格式 evidence_ids/quote。"
        )
    return [
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
                f"{retry_instruction}"
            ),
        ),
        Message(
            role="user",
            content=(
                f"问题：{question}\n\n"
                f"{build_evidence_context(evidence)}"
            ),
        ),
    ]


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
        messages = _build_messages(question, evidence)
        for attempt in range(2):
            response = await self.provider.complete(
                messages, self.model, tools=[]
            )
            try:
                payload = _validate_payload(response.content or "")
            except (
                ValidationError,
                ValueError,
                json.JSONDecodeError,
            ) as structure_error:
                if attempt == 1:
                    raise KnowledgeError(
                        "generation_invalid_output"
                    ) from structure_error
                messages = _build_messages(
                    question,
                    evidence,
                    retry_reason=_retry_reason(structure_error),
                )
                continue
            try:
                citations = assemble_citations(payload.claims, evidence)
            except KnowledgeError as citation_error:
                if (
                    citation_error.code != "citation_validation_failed"
                    or attempt == 1
                ):
                    raise
                messages = _build_messages(
                    question,
                    evidence,
                    retry_reason="citation_validation_failed",
                )
                continue
            return RagAnswer(
                status=payload.status,
                answer=payload.answer,
                claims=payload.claims,
                citations=citations,
            )
        raise AssertionError("unreachable")
