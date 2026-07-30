from starter_agent.domain.models import ModelResponse
from starter_agent.job_research.location_alias import LocationAliasBuilder


class _Provider:
    name = "alias-test"

    def __init__(self, contents):
        self.contents = list(contents)
        self.messages = []

    async def complete(self, messages, model, tools, **kwargs):
        self.messages.append(messages)
        return ModelResponse(
            content=self.contents.pop(0),
            provider=self.name,
            model=model,
        )


async def test_location_alias_accepts_strict_latin_json_without_resume_data():
    provider = _Provider(['```json\n{"location_alias":"Shanghai"}\n```'])

    alias = await LocationAliasBuilder().build(
        location="上海", provider=provider, model="test-model"
    )

    assert alias == "Shanghai"
    serialized = str(provider.messages)
    assert "上海" in serialized
    assert "resume" not in serialized.casefold()
    assert "evidence" not in serialized.casefold()


async def test_location_alias_retries_invalid_json_once():
    provider = _Provider(["Shanghai", '{"location_alias":"Shanghai"}'])

    alias = await LocationAliasBuilder().build(
        location="上海", provider=provider, model="test-model"
    )

    assert alias == "Shanghai"
    assert len(provider.messages) == 2


async def test_location_alias_rejects_non_latin_same_value_and_extra_fields():
    for content in (
        '{"location_alias":"上海"}',
        '{"location_alias":"Paris"}',
        '{"location_alias":"Shanghai","secret":"x"}',
        '{"location_alias":"' + ("A" * 101) + '"}',
    ):
        provider = _Provider([content, content])
        original = "Paris" if "Paris" in content else "上海"
        assert (
            await LocationAliasBuilder().build(
                location=original, provider=provider, model="test-model"
            )
            is None
        )


async def test_location_alias_provider_failure_degrades_to_none():
    class _FailingProvider(_Provider):
        async def complete(self, messages, model, tools, **kwargs):
            raise RuntimeError("provider unavailable")

    assert (
        await LocationAliasBuilder().build(
            location="上海",
            provider=_FailingProvider([]),
            model="test-model",
        )
        is None
    )
