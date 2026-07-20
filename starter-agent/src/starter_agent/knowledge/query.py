from __future__ import annotations

import re
from dataclasses import dataclass

from starter_agent.knowledge.errors import KnowledgeError


_SYNONYMS = {
    "jd": ["职位描述", "岗位要求"],
    "职位描述": ["JD", "岗位要求"],
    "岗位要求": ["JD", "职位描述"],
    "rag": ["检索增强", "知识库"],
    "检索增强": ["RAG", "知识库"],
    "知识库": ["RAG", "检索增强"],
    "llm": ["大语言模型"],
    "大语言模型": ["LLM"],
}


@dataclass(frozen=True)
class NormalizedQuery:
    terms: list[str]
    match_expression: str | None
    short_terms: list[str]


def normalize_query(question: str) -> NormalizedQuery:
    clean = " ".join(question.replace("\x00", " ").split()).strip()
    if not clean:
        raise KnowledgeError("knowledge_query_invalid")
    raw_terms = re.findall(r"[\w\u3400-\u9fff-]+", clean, flags=re.UNICODE)
    terms: list[str] = []
    for term in raw_terms:
        if term.casefold() in {"or", "and", "not", "near"}:
            continue
        for value in [term, *_SYNONYMS.get(term.casefold(), [])]:
            if value not in terms:
                terms.append(value)
    long_terms = [term for term in terms if len(term) >= 3]
    short_terms = [term for term in terms if 1 <= len(term) < 3]
    expression = (
        " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in long_terms)
        if long_terms
        else None
    )
    return NormalizedQuery(terms, expression, short_terms)
