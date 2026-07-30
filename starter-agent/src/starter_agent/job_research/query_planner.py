from __future__ import annotations

import re
from dataclasses import dataclass

from starter_agent.tools.adapters.serpapi_location import LocationResolution


_LANGUAGE = re.compile(r"^[a-z]{2}(?:-[a-z]{2})?$", re.IGNORECASE)
_ROLE_QUERY_PAIRS: tuple[tuple[str, str], ...] = (
    ("AI Agent 工程师 招聘", "AI Agent 工程师 招聘"),
    ("智能体工程师 招聘", "智能体 Engineer jobs"),
    ("大模型应用工程师 招聘", "大模型应用工程师 招聘"),
    ("生成式 AI 工程师 招聘", "Generative AI Engineer jobs"),
    ("AI Agent Engineer jobs", "AI Agent Engineer jobs"),
    ("LLM Application Engineer jobs", "LLM Application Engineer jobs"),
)


@dataclass(frozen=True, slots=True)
class JobQueryPlan:
    queries: tuple[str, ...]
    location_aliases: tuple[str, ...]
    canonical_location: str
    hl: str | None
    gl: str | None
    reason_codes: tuple[str, ...]


def build_job_query_plan(
    query: str,
    requested_location: str,
    resolution: LocationResolution,
    user_language: str,
) -> JobQueryPlan:
    del query  # The bounded public role vocabulary prevents resume-text leakage.
    original = " ".join(requested_location.split())[:100]
    aliases = _deduplicate((original, resolution.city_alias or ""))
    reasons: list[str] = ["location_alias_expanded"]
    if not resolution.canonical_name or len(aliases) < 2:
        reasons = ["location_alias_degraded"]

    queries: list[str] = []
    for original_role, canonical_role in _ROLE_QUERY_PAIRS:
        for index, alias in enumerate(aliases):
            role = original_role if index == 0 else canonical_role
            candidate = f"{alias} {role}".strip()
            if candidate and candidate not in queries:
                queries.append(candidate[:300])
            if len(queries) == 12:
                break
        if len(queries) == 12:
            break

    language = user_language.strip().casefold()
    hl = language if _LANGUAGE.fullmatch(language) else None
    gl = resolution.country_code
    return JobQueryPlan(
        queries=tuple(queries),
        location_aliases=aliases,
        canonical_location=resolution.canonical_name or "",
        hl=hl,
        gl=gl,
        reason_codes=tuple(reasons),
    )


def _deduplicate(values: tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = " ".join(value.split())
        key = normalized.casefold()
        if normalized and key not in seen:
            seen.add(key)
            result.append(normalized)
    return tuple(result)
