from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Any


@dataclass(frozen=True)
class CompanyAttribution:
    company: str = ""
    source: str = ""
    confidence: str = ""


_EXPLICIT_TITLE_PATTERNS = (
    re.compile(
        r"招聘\s*[_｜|:]\s*(?P<company>[\u3400-\u9fffA-Za-z0-9（）()·&. -]{2,60}?)"
        r"\s*招聘(?:\s*[-|｜].*)?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|[-|｜]\s)(?P<company>[\u3400-\u9fffA-Za-z0-9（）()·&. -]{2,60}?)"
        r"\s+招聘\s*[-|｜:]\s*[^\r\n]+$",
        re.IGNORECASE,
    ),
)
_PLATFORM_NAMES = frozenset(
    {
        "猎聘",
        "猎聘网",
        "智联",
        "智联招聘",
        "前程无忧",
        "51job",
        "boss直聘",
        "boss",
        "linkedin",
        "builtin",
        "jobs",
        "careers",
    }
)
_GENERIC_COMPANY = re.compile(r"^(?:未知公司|保密|某公司|招聘|职位|岗位)$", re.IGNORECASE)
_SOURCE_PRIORITY = {
    "page_json_ld": 5,
    "page_html": 4,
    "google_jobs": 3,
    "verified_domain": 2,
    "organic_explicit": 1,
}
_CONFIDENCE_PRIORITY = {"high": 2, "medium": 1}


def infer_organic_company(title: str, snippet: str = "") -> CompanyAttribution:
    del snippet  # Reserved for future explicit, source-backed patterns.
    normalized = " ".join(title.split())
    for pattern in _EXPLICIT_TITLE_PATTERNS:
        match = pattern.search(normalized)
        if match is None:
            continue
        company = _clean_company(match.group("company"))
        if company:
            return CompanyAttribution(
                company=company,
                source="organic_explicit",
                confidence="medium",
            )
    return CompanyAttribution()


def preferred_company_attribution(
    *items: Mapping[str, Any],
) -> CompanyAttribution:
    candidates = [item for item in items if str(item.get("company") or "").strip()]
    if not candidates:
        return CompanyAttribution()
    selected = max(
        candidates,
        key=lambda item: (
            _effective_confidence(item),
            _SOURCE_PRIORITY.get(str(item.get("company_source") or ""), 0),
        ),
    )
    return CompanyAttribution(
        company=str(selected.get("company") or "").strip(),
        source=str(selected.get("company_source") or "").strip(),
        confidence=str(selected.get("company_confidence") or "").strip(),
    )


def _effective_confidence(item: Mapping[str, Any]) -> int:
    confidence = _CONFIDENCE_PRIORITY.get(
        str(item.get("company_confidence") or ""),
        0,
    )
    if confidence:
        return confidence
    if str(item.get("url_kind") or "") in {"structured_apply", "structured_share"}:
        return _CONFIDENCE_PRIORITY["high"]
    return 0


def _clean_company(value: str) -> str:
    company = value.strip(" _-|｜:：")
    folded = company.casefold()
    if (
        not company
        or len(company) > 60
        or folded in _PLATFORM_NAMES
        or _GENERIC_COMPANY.fullmatch(company)
    ):
        return ""
    return company
