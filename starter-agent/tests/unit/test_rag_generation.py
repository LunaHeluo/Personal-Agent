import json
from uuid import uuid4

import pytest

from starter_agent.domain.models import ModelResponse
from starter_agent.knowledge.errors import KnowledgeError
from starter_agent.knowledge.generation import RagGenerator
from starter_agent.knowledge.models import Evidence


class StubProvider:
    name = "stub"

    def __init__(self, content: str | None = None) -> None:
        self.tools = None
        self.content = content

    async def complete(self, messages, model, tools, **kwargs):
        self.tools = tools
        return ModelResponse(
            content=self.content or _valid_payload(),
            provider="stub",
            model=model,
        )


def _valid_payload() -> str:
    return json.dumps(
        {
            "status": "answered",
            "answer": "候选人熟练 Python。",
            "claims": [
                {
                    "text": "候选人熟练 Python。",
                    "evidence_ids": ["E1"],
                    "quote": "熟练 Python",
                }
            ],
        },
        ensure_ascii=False,
    )


def _evidence() -> Evidence:
    return Evidence(
        evidence_id="E1",
        chunk_id=uuid4(),
        document_id=uuid4(),
        filename="resume.md",
        version=1,
        start_line=2,
        end_line=2,
        text="熟练 Python。",
    )


@pytest.mark.asyncio
async def test_generation_uses_no_tools_and_returns_validated_citations() -> None:
    provider = StubProvider()
    evidence = _evidence()

    answer = await RagGenerator(provider, "test").generate(
        "会 Python 吗？", [evidence]
    )

    assert provider.tools == []
    assert answer.status == "answered"
    assert answer.citations[0].chunk_id == evidence.chunk_id


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["json", ""])
async def test_generation_accepts_one_strict_json_fence(
    language: str,
) -> None:
    provider = StubProvider(f"```{language}\n{_valid_payload()}\n```")

    answer = await RagGenerator(provider, "test").generate(
        "会 Python 吗？", [_evidence()]
    )

    assert answer.status == "answered"
    assert len(answer.citations) == 1


@pytest.mark.asyncio
async def test_generation_rejects_prose_around_json() -> None:
    provider = StubProvider(f"以下是结果：\n{_valid_payload()}")

    with pytest.raises(KnowledgeError) as exc_info:
        await RagGenerator(provider, "test").generate(
            "会 Python 吗？", [_evidence()]
        )

    assert exc_info.value.code == "generation_invalid_output"


@pytest.mark.asyncio
async def test_generation_rejects_multiple_json_fences() -> None:
    fenced = f"```json\n{_valid_payload()}\n```"
    provider = StubProvider(f"{fenced}\n{fenced}")

    with pytest.raises(KnowledgeError) as exc_info:
        await RagGenerator(provider, "test").generate(
            "会 Python 吗？", [_evidence()]
        )

    assert exc_info.value.code == "generation_invalid_output"
