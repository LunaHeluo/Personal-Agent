from starter_agent.knowledge.query import normalize_query


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
