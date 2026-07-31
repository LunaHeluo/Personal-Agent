from __future__ import annotations

import re
from dataclasses import dataclass

from starter_agent.tools.adapters.serpapi_location import LocationResolution


_LANGUAGE = re.compile(r"^[a-z]{2}(?:-[a-z]{2})?$", re.IGNORECASE)
_PRIVATE_OR_LONG_QUERY = re.compile(
    r"(?:https?://|www\.|[\w.+-]+@[\w.-]+\.[a-z]{2,}|\b1[3-9]\d{9}\b)",
    re.IGNORECASE,
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
    original = " ".join(requested_location.split())[:100]
    aliases = _deduplicate((original, resolution.city_alias or ""))
    reasons: list[str] = [
        "location_alias_expanded",
        "detail_evidence_queries",
    ]
    if not resolution.canonical_name or len(aliases) < 2:
        reasons[0] = "location_alias_degraded"

    primary_location = aliases[0] if aliases else original
    latin_location = aliases[1] if len(aliases) > 1 else primary_location
    safe_role = _safe_role_phrase(
        query,
        location_terms=(original, resolution.city_alias or ""),
    )
    role_pairs = [
        (primary_location, "AI Agent 工程师 招聘"),
        (latin_location, "AI Agent Engineer jobs"),
        (primary_location, "智能体 工程师 招聘"),
        (latin_location, "LLM Application Engineer jobs"),
        (primary_location, "大模型应用工程师 招聘"),
        (latin_location, "Generative AI Engineer jobs"),
        (primary_location, "AI Agent 岗位职责 任职要求"),
        (
            latin_location,
            "AI Agent Engineer responsibilities requirements",
        ),
        (primary_location, "智能体 职位描述 招聘"),
        (
            latin_location,
            "LLM Application Engineer job description careers",
        ),
    ]
    if safe_role:
        role_pairs.extend(
            (
                (primary_location, f"{safe_role} 招聘"),
                (latin_location, f"{safe_role} jobs"),
            )
        )

    queries = _deduplicate(
        tuple(f"{location} {role}".strip()[:300] for location, role in role_pairs)
    )[:12]
    language = user_language.strip().casefold()
    hl = language if _LANGUAGE.fullmatch(language) else None
    return JobQueryPlan(
        queries=queries,
        location_aliases=aliases,
        canonical_location=resolution.canonical_name or "",
        hl=hl,
        gl=resolution.country_code,
        reason_codes=tuple(reasons),
    )


def _safe_role_phrase(query: str, *, location_terms: tuple[str, ...]) -> str:
    normalized = " ".join(query.replace("\r", " ").replace("\n", " ").split())
    if (
        not normalized
        or len(normalized) > 80
        or len(normalized.split()) > 10
        or _PRIVATE_OR_LONG_QUERY.search(normalized)
    ):
        return ""
    for location in location_terms:
        if location:
            normalized = re.sub(
                re.escape(location),
                " ",
                normalized,
                flags=re.IGNORECASE,
            )
    normalized = " ".join(normalized.split()).strip(" ,，。;；:-")
    return normalized[:80]


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
