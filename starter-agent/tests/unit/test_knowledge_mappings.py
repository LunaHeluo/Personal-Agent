import pytest
from pydantic import ValidationError

from starter_agent.settings import QueryMappingConfig
from starter_agent.knowledge.mappings import build_query_mapping_catalog


def test_builtin_catalog_expands_chinese_and_english_both_ways() -> None:
    catalog = build_query_mapping_catalog(QueryMappingConfig())

    assert "岗位要求" in catalog.expand("JD")
    assert "JD" in catalog.expand("岗位要求")
    assert catalog.version == "builtin-v1"


def test_yaml_groups_replace_add_and_disable() -> None:
    config = QueryMappingConfig.model_validate(
        {
            "version": "local-v1",
            "groups": {
                "rag": ["RAG", "知识检索"],
                "agent_platform": ["Agent 平台", "智能体平台"],
            },
            "disabled_groups": ["llm"],
        }
    )

    catalog = build_query_mapping_catalog(config)

    assert catalog.expand("RAG") == ("RAG", "知识检索")
    assert "智能体平台" in catalog.expand("Agent 平台")
    assert catalog.expand("LLM") == ("LLM",)
    assert catalog.version == "local-v1"


@pytest.mark.parametrize(
    "payload",
    [
        {"groups": {"rag": ["RAG", "知识检索"]}},
        {"version": "local-v1", "groups": {"bad": ["OR", "或者"]}},
        {
            "version": "local-v1",
            "groups": {
                "first": ["same", "甲"],
                "second": ["SAME", "乙"],
            },
        },
    ],
)
def test_invalid_mapping_configuration_is_rejected(payload) -> None:
    with pytest.raises(ValidationError):
        QueryMappingConfig.model_validate(payload)
