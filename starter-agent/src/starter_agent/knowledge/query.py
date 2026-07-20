from __future__ import annotations

import re
from dataclasses import dataclass

from starter_agent.knowledge.errors import KnowledgeError
from starter_agent.knowledge.mappings import (
    QueryMappingCatalog,
    build_query_mapping_catalog,
)
from starter_agent.settings import QueryMappingConfig


_RESERVED = {"or", "and", "not", "near"}
_COMPARISON_TERMS = ("匹配", "适合", "胜任")


@dataclass(frozen=True)
class NormalizedQuery:
    terms: list[str]
    match_expression: str | None
    short_terms: list[str]
    comparison_intent: bool = False
    mapping_version: str = "builtin-v1"


def normalize_query(
    question: str,
    catalog: QueryMappingCatalog | None = None,
) -> NormalizedQuery:
    clean = " ".join(question.replace("\x00", " ").split()).strip()
    if not clean:
        raise KnowledgeError("knowledge_query_invalid")
    catalog = catalog or build_query_mapping_catalog(QueryMappingConfig())
    raw_terms = re.findall(
        r"(?<![A-Za-z0-9._+\-\u3400-\u9fff])"
        r"[A-Za-z0-9][A-Za-z0-9._+-]*",
        clean,
    )
    raw_terms.extend(
        value
        for value in re.findall(r"[\u3400-\u9fff]+", clean)
        if len(value) <= 2
    )
    raw_terms.extend(
        phrase
        for phrase in catalog.phrases
        if phrase.casefold() in clean.casefold()
    )
    raw_terms.extend(term for term in _COMPARISON_TERMS if term in clean)

    terms: list[str] = []
    seen: set[str] = set()
    for term in raw_terms:
        if term.casefold() in _RESERVED:
            continue
        for value in catalog.expand(term):
            folded = value.casefold()
            if folded not in seen:
                seen.add(folded)
                terms.append(value)
    long_terms = [term for term in terms if len(term) >= 3]
    short_terms = [term for term in terms if 1 <= len(term) < 3]
    expression = (
        " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in long_terms)
        if long_terms
        else None
    )
    has_resume = any(
        term.casefold() in {value.casefold() for value in catalog.expand("简历")}
        for term in terms
    )
    has_job = any(
        term.casefold() in {value.casefold() for value in catalog.expand("岗位")}
        for term in terms
    )
    comparison_intent = (
        has_resume
        and has_job
        and any(term in clean for term in _COMPARISON_TERMS)
    )
    return NormalizedQuery(
        terms,
        expression,
        short_terms,
        comparison_intent=comparison_intent,
        mapping_version=catalog.version,
    )
