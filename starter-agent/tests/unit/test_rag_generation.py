import json
from uuid import uuid4

import pytest

from starter_agent.domain.errors import ProviderTimeoutError
from starter_agent.domain.models import ModelResponse
from starter_agent.knowledge.errors import KnowledgeError
from starter_agent.knowledge.generation import RagGenerator
from starter_agent.knowledge.models import Evidence


class StubProvider:
    name = "stub"

    def __init__(self, *contents: str) -> None:
        self.contents = list(contents) or [_valid_payload()]
        self.fallback_content = self.contents[-1]
        self.calls = []
        self.tools = None
        self.messages = None

    async def complete(self, messages, model, tools, **kwargs):
        self.tools = tools
        self.messages = messages
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": tools,
            }
        )
        content = (
            self.contents.pop(0)
            if self.contents
            else self.fallback_content
        )
        return ModelResponse(
            content=content,
            provider="stub",
            model=model,
        )


class TimeoutProvider:
    name = "timeout"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages, model, tools, **kwargs):
        self.calls += 1
        raise ProviderTimeoutError(
            provider=self.name,
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
                    "evidence_refs": [
                        {
                            "evidence_id": "E1",
                            "quote": "熟练 Python",
                        }
                    ],
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
async def test_generation_prompt_defines_the_strict_status_contract() -> None:
    provider = StubProvider()

    await RagGenerator(provider, "test").generate(
        "会 Python 吗？", [_evidence()]
    )

    system_prompt = provider.messages[0].content
    assert '"answered"、"refused"、"conflict"' in system_prompt
    assert '"success"' in system_prompt
    assert "不要输出" in system_prompt
    assert '"evidence_refs"' in system_prompt
    assert '"evidence_id"' in system_prompt
    assert "每个引用项只对应一个 Evidence" in system_prompt


@pytest.mark.asyncio
async def test_generation_still_accepts_legacy_single_evidence_claim() -> None:
    legacy = json.dumps(
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

    answer = await RagGenerator(
        StubProvider(legacy), "test"
    ).generate("会 Python 吗？", [_evidence()])

    assert answer.status == "answered"
    assert answer.claims[0].evidence_refs[0].evidence_id == "E1"


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


@pytest.mark.asyncio
async def test_valid_first_response_does_not_retry() -> None:
    provider = StubProvider(_valid_payload())

    answer = await RagGenerator(provider, "test").generate(
        "会 Python 吗？",
        [_evidence()],
    )

    assert answer.status == "answered"
    assert len(provider.calls) == 1
    assert provider.calls[0]["tools"] == []


@pytest.mark.asyncio
async def test_invalid_first_response_retries_once() -> None:
    provider = StubProvider(
        "我认为候选人熟练 Python。",
        _valid_payload(),
    )

    answer = await RagGenerator(provider, "test").generate(
        "会 Python 吗？",
        [_evidence()],
    )

    assert answer.status == "answered"
    assert len(provider.calls) == 2
    retry_messages = provider.calls[1]["messages"]
    retry_prompt = "\n".join(
        message.content for message in retry_messages
    )
    assert "只输出一个 JSON 对象" in retry_prompt
    assert '"evidence_refs"' in retry_prompt
    assert '"evidence_id"' in retry_prompt
    assert '"quote"' in retry_prompt
    assert "熟练 Python" in retry_prompt
    assert provider.calls[1]["tools"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_payload",
    [
        json.dumps(
            {
                "status": "answered",
                "answer": "候选人熟练 Python。",
            },
            ensure_ascii=False,
        ),
        json.dumps(
            {
                "status": "answered",
                "answer": "候选人熟练 Python。",
                "claims": [
                    {
                        "text": "候选人熟练 Python。",
                        "evidence_refs": [
                            {
                                "evidence_id": "E1",
                                "quote": "熟练 Python",
                            }
                        ],
                        "evidence_ids": ["E1"],
                        "quote": "熟练 Python",
                    }
                ],
            },
            ensure_ascii=False,
        ),
    ],
)
async def test_schema_errors_retry_once(
    invalid_payload: str,
) -> None:
    provider = StubProvider(
        invalid_payload,
        _valid_payload(),
    )

    answer = await RagGenerator(provider, "test").generate(
        "会 Python 吗？",
        [_evidence()],
    )

    assert answer.status == "answered"
    assert len(provider.calls) == 2


@pytest.mark.asyncio
async def test_second_invalid_response_does_not_call_third_time() -> None:
    provider = StubProvider(
        "first invalid response",
        "second invalid response",
    )

    with pytest.raises(KnowledgeError) as error:
        await RagGenerator(provider, "test").generate(
            "会 Python 吗？",
            [_evidence()],
        )

    assert error.value.code == "generation_invalid_output"
    assert len(provider.calls) == 2


@pytest.mark.asyncio
async def test_provider_error_is_not_retried() -> None:
    provider = TimeoutProvider()

    with pytest.raises(ProviderTimeoutError):
        await RagGenerator(provider, "test").generate(
            "会 Python 吗？",
            [_evidence()],
        )

    assert provider.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reference",
    [
        {"evidence_id": "E2", "quote": "熟练 Python"},
        {"evidence_id": "E1", "quote": "改写后的 Python 能力"},
    ],
)
async def test_citation_error_retries_once_with_strict_validation(
    reference: dict[str, str],
) -> None:
    invalid_payload = json.dumps(
        {
            "status": "answered",
            "answer": "候选人熟练 Python。",
            "claims": [
                {
                    "text": "候选人熟练 Python。",
                    "evidence_refs": [reference],
                }
            ],
        },
        ensure_ascii=False,
    )
    provider = StubProvider(invalid_payload, _valid_payload())

    answer = await RagGenerator(provider, "test").generate(
        "会 Python 吗？",
        [_evidence()],
    )

    assert answer.status == "answered"
    assert len(provider.calls) == 2
    retry_prompt = "\n".join(
        message.content for message in provider.calls[1]["messages"]
    )
    assert "引用" in retry_prompt
    assert "连续原文" in retry_prompt


@pytest.mark.asyncio
async def test_second_citation_error_does_not_call_third_time() -> None:
    invalid_payload = json.dumps(
        {
            "status": "answered",
            "answer": "候选人熟练 Python。",
            "claims": [
                {
                    "text": "候选人熟练 Python。",
                    "evidence_refs": [
                        {
                            "evidence_id": "E1",
                            "quote": "改写后的 Python 能力",
                        }
                    ],
                }
            ],
        },
        ensure_ascii=False,
    )
    provider = StubProvider(invalid_payload, invalid_payload)

    with pytest.raises(KnowledgeError) as error:
        await RagGenerator(provider, "test").generate(
            "会 Python 吗？",
            [_evidence()],
        )

    assert error.value.code == "citation_validation_failed"
    assert len(provider.calls) == 2
