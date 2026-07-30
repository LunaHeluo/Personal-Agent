from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from starter_agent.job_research.candidates import rank_job_candidates


_CHINESE = re.compile(r"[\u3400-\u9fff]")


@dataclass(frozen=True, slots=True)
class LocationRecallReport:
    before_chinese_title_count: int
    after_chinese_title_count: int
    raw_result_count: int
    deduplicated_count: int
    top_ten: tuple[dict[str, Any], ...]


def compare_location_recall(fixture: Mapping[str, Any]) -> LocationRecallReport:
    before = _rows(fixture.get("before_results"))
    expanded = _rows(fixture.get("expanded_results"))
    aliases = tuple(
        str(value)
        for value in fixture.get("location_aliases", [])
        if isinstance(value, str)
    )
    ranked = rank_job_candidates(
        expanded,
        limit=10,
        location_aliases=aliases,
    )
    top_ten = tuple(
        {
            "title": item.title,
            "url": item.url,
            "score": item.score,
            "page_kind": item.page_kind,
            "reason_codes": list(item.reason_codes),
            "matched_queries": list(item.matched_queries),
            "search_engines": list(item.search_engines),
        }
        for item in ranked
    )
    return LocationRecallReport(
        before_chinese_title_count=_chinese_title_count(before),
        after_chinese_title_count=sum(
            1 for item in ranked if _CHINESE.search(item.title)
        ),
        raw_result_count=len(expanded),
        deduplicated_count=len(ranked),
        top_ten=top_ten,
    )


def _rows(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _chinese_title_count(rows: list[Mapping[str, Any]]) -> int:
    return sum(
        1 for item in rows
        if _CHINESE.search(str(item.get("title") or ""))
    )
