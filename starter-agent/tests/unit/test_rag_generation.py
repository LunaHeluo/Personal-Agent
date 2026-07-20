import json
from uuid import uuid4

import pytest

from starter_agent.domain.models import ModelResponse
from starter_agent.knowledge.generation import RagGenerator
from starter_agent.knowledge.models import Evidence


class StubProvider:
    name = "stub"

    def __init__(self) -> None:
        self.tools = None

    async def complete(self, messages, model, tools, **kwargs):
        self.tools = tools
        return ModelResponse(
            content=json.dumps(
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
            ),
            provider="stub",
            model=model,
        )


@pytest.mark.asyncio
async def test_generation_uses_no_tools_and_returns_validated_citations() -> None:
    provider = StubProvider()
    evidence = Evidence(
        evidence_id="E1",
        chunk_id=uuid4(),
        document_id=uuid4(),
        filename="resume.md",
        version=1,
        start_line=2,
        end_line=2,
        text="熟练 Python。",
    )

    answer = await RagGenerator(provider, "test").generate(
        "会 Python 吗？", [evidence]
    )

    assert provider.tools == []
    assert answer.status == "answered"
    assert answer.citations[0].chunk_id == evidence.chunk_id
