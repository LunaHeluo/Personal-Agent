from starter_agent.knowledge.query import normalize_query
from starter_agent.knowledge.mappings import build_query_mapping_catalog
from starter_agent.settings import QueryMappingConfig


def test_query_normalization_adds_explicit_synonyms_and_escapes_fts() -> None:
    query = normalize_query('RAG "岗位要求" OR')

    assert "检索增强" in query.terms
    assert "知识库" in query.terms
    assert '"岗位要求"' in query.match_expression
    assert "OR" not in query.terms
    assert " OR " in query.match_expression


def test_short_query_uses_bounded_fallback() -> None:
    query = normalize_query("薪")

    assert query.match_expression is None
    assert query.short_terms == ["薪"]


def test_chinese_resume_job_question_extracts_domain_terms() -> None:
    query = normalize_query("我的简历匹配哪个岗位")

    assert "我的简历匹配哪个岗位" not in query.terms
    assert {"简历", "匹配", "岗位"}.issubset(query.terms)
    assert query.comparison_intent is True
    assert query.mapping_version == "builtin-v1"
    assert query.required_terms == []


def test_unmapped_literal_terms_remain_required_retrieval_anchors() -> None:
    query = normalize_query("Aurora 招聘知识库")

    assert query.required_terms == ["Aurora"]
    assert {"RAG", "知识库"}.issubset(query.terms)


def test_yaml_catalog_expands_mixed_language_terms() -> None:
    catalog = build_query_mapping_catalog(
        QueryMappingConfig.model_validate(
            {
                "version": "local-v1",
                "groups": {
                    "agent_platform": ["Agent 平台", "智能体平台"],
                },
            }
        )
    )

    query = normalize_query("Agent 平台需要哪些能力", catalog)

    assert "智能体平台" in query.terms
    assert query.mapping_version == "local-v1"
