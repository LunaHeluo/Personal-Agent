from uuid import uuid4

import pytest

from starter_agent.domain.models import ModelResponse
from starter_agent.job_research.search_profile import (
    JobSearchProfileBuilder,
    SearchProfileUnavailable,
)
from starter_agent.knowledge.models import Evidence


class FakeProvider:
    name = "fake"

    def __init__(self, contents):
        self.contents = list(contents)
        self.calls = []

    async def complete(self, messages, model, tools, **kwargs):
        self.calls.append({"messages": messages, "model": model, "tools": tools})
        return ModelResponse(
            content=self.contents.pop(0),
            provider="fake",
            model=model,
        )

    async def health(self, model):
        return True, "ok"


def evidence(text="熟悉 Python、FastAPI 和 RAG 系统开发。"):
    return Evidence(
        evidence_id="E1",
        chunk_id=uuid4(),
        document_id=uuid4(),
        filename="resume.md",
        version=1,
        section_path=["技能"],
        start_line=1,
        end_line=2,
        text=text,
    )


async def test_profile_builder_returns_schema_valid_public_search_profile():
    provider = FakeProvider(
        [
            '{"query":"Python backend engineer","location":"深圳",'
            '"evidence_refs":["E1"]}'
        ]
    )
    builder = JobSearchProfileBuilder()

    result = await builder.build(
        user_request="根据我的简历查询深圳的岗位",
        evidence=[evidence()],
        provider=provider,
        model="test-model",
    )

    assert result.query == "Python backend engineer"
    assert result.location == "深圳"
    assert result.evidence_refs == ("E1",)
    assert provider.calls[0]["tools"] == []
    prompt = provider.calls[0]["messages"][1].content
    assert "resume.md" not in prompt
    assert str(result.evidence_refs) not in prompt


async def test_profile_builder_retries_then_rejects_private_search_text():
    private = (
        '{"query":"我在 Example 公司负责 Python 平台开发",'
        '"location":"深圳","evidence_refs":["E1"]}'
    )
    provider = FakeProvider([private, private])

    with pytest.raises(SearchProfileUnavailable, match="unsafe_search_profile"):
        await JobSearchProfileBuilder().build(
            user_request="根据我的简历找岗位",
            evidence=[evidence()],
            provider=provider,
            model="test-model",
        )

    assert len(provider.calls) == 2


async def test_profile_builder_rejects_name_and_employer_from_referenced_evidence():
    leaked = (
        '{"query":"alice acme Python engineer","location":null,'
        '"evidence_refs":["E1"]}'
    )
    provider = FakeProvider([leaked, leaked])

    with pytest.raises(SearchProfileUnavailable, match="unsafe_search_profile"):
        await JobSearchProfileBuilder().build(
            user_request="find matching jobs",
            evidence=[evidence("Alice worked at Acme building Python services")],
            provider=provider,
            model="test-model",
        )

    assert len(provider.calls) == 2


async def test_profile_builder_requires_resume_evidence_before_model_call():
    provider = FakeProvider([])

    with pytest.raises(SearchProfileUnavailable, match="no_resume_evidence"):
        await JobSearchProfileBuilder().build(
            user_request="根据我的简历找岗位",
            evidence=[],
            provider=provider,
            model="test-model",
        )

    assert provider.calls == []


async def test_profile_builder_retries_invalid_json_with_a_valid_contract_example():
    provider = FakeProvider(
        [
            "我建议搜索 Python 后端岗位。",
            '{"query":"Python backend engineer","location":"深圳",'
            '"evidence_refs":["E1"]}',
        ]
    )

    result = await JobSearchProfileBuilder().build(
        user_request="根据我的简历查询深圳的岗位",
        evidence=[evidence()],
        provider=provider,
        model="test-model",
    )

    assert result.query == "Python backend engineer"
    retry_prompt = provider.calls[1]["messages"][0].content
    assert "invalid_json" in retry_prompt
    assert '"query"' in retry_prompt
    assert '"location"' in retry_prompt
    assert '"evidence_refs"' in retry_prompt


async def test_profile_builder_reports_invalid_json_after_retry_exhaustion():
    provider = FakeProvider(["不是 JSON", "仍然不是 JSON"])

    with pytest.raises(
        SearchProfileUnavailable,
        match="invalid_json",
    ) as captured:
        await JobSearchProfileBuilder().build(
            user_request="根据我的简历找岗位",
            evidence=[evidence()],
            provider=provider,
            model="test-model",
        )

    assert [item.error_code for item in captured.value.attempts] == [
        "invalid_json",
        "invalid_json",
    ]
    assert [item.attempt for item in captured.value.attempts] == [1, 2]
    assert all(item.model_request_id for item in captured.value.attempts)
    assert all(item.output_length > 0 for item in captured.value.attempts)
    assert all(item.fields == () for item in captured.value.attempts)
    assert all(not hasattr(item, "content") for item in captured.value.attempts)


async def test_profile_builder_reports_schema_validation_failure_separately():
    malformed = (
        '{"query":"Python backend engineer","location":"上海",'
        '"evidence_refs":"private invalid value"}'
    )
    provider = FakeProvider([malformed, malformed])

    with pytest.raises(
        SearchProfileUnavailable,
        match="schema_validation_failed",
    ) as captured:
        await JobSearchProfileBuilder().build(
            user_request="根据我的简历找岗位",
            evidence=[evidence()],
            provider=provider,
            model="test-model",
        )

    assert [item.issues for item in captured.value.attempts] == [
        ("evidence_refs:list_type",),
        ("evidence_refs:list_type",),
    ]
    assert "private invalid value" not in str(captured.value.attempts)


async def test_profile_builder_carries_explicit_freshness_without_tools():
    provider = FakeProvider(
        [
            '{"query":"Python backend engineer","location":"Berlin",'
            '"evidence_refs":["E1"],"explicit_freshness":true}'
        ]
    )

    result = await JobSearchProfileBuilder().build(
        user_request="查 Berlin 当前招聘的 Python 岗位",
        evidence=[evidence()],
        provider=provider,
        model="test-model",
    )

    assert result.explicit_freshness is True
    assert result.role_terms == ("Python", "backend", "engineer")
    assert provider.calls[0]["tools"] == []
