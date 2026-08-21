from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from starter_agent.settings import QueryMappingConfig


_BUILTIN_GROUPS: dict[str, tuple[str, ...]] = {
    "job_description": ("JD", "职位描述", "岗位描述", "职位要求", "岗位要求", "岗位"),
    "rag": ("RAG", "检索增强", "知识库"),
    "llm": ("LLM", "大语言模型", "大模型"),
    "resume": ("resume", "CV", "简历"),
    "experience": ("experience", "经历", "项目经历", "工作经历"),
    "skill": ("skill", "skills", "技能", "能力"),
}


@dataclass(frozen=True)
class QueryMappingCatalog:
    version: str
    groups: Mapping[str, tuple[str, ...]]
    reverse: Mapping[str, tuple[str, ...]]

    def expand(self, term: str) -> tuple[str, ...]:
        return self.reverse.get(term.casefold(), (term,))

    @property
    def phrases(self) -> tuple[str, ...]:
        values = {term for terms in self.groups.values() for term in terms}
        return tuple(sorted(values, key=lambda value: (-len(value), value.casefold())))


def build_query_mapping_catalog(config: QueryMappingConfig) -> QueryMappingCatalog:
    groups = dict(_BUILTIN_GROUPS)
    groups.update({key: tuple(values) for key, values in config.groups.items()})
    for group_id in config.disabled_groups:
        groups.pop(group_id, None)
    reverse: dict[str, tuple[str, ...]] = {}
    for group_id, terms in groups.items():
        for term in terms:
            folded = term.casefold()
            if folded in reverse:
                raise ValueError(
                    f"query mapping term belongs to multiple groups: {group_id}"
                )
            reverse[folded] = terms
    return QueryMappingCatalog(
        version=config.version,
        groups=MappingProxyType(groups),
        reverse=MappingProxyType(reverse),
    )
