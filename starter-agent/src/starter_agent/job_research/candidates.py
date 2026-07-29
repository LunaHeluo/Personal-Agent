from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any, Literal
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict


UrlKind = Literal["structured_apply", "structured_share", "organic"]
CandidatePageKind = Literal[
    "job_detail_candidate",
    "collection_page",
    "social_or_content_page",
    "invalid_or_unsafe_url",
    "unknown_candidate",
]

_KIND_PRIORITY: dict[str, tuple[int, float]] = {
    "structured_apply": (0, 1.0),
    "structured_share": (1, 0.7),
    "organic": (2, 0.4),
}
_COLLECTION_TITLE = re.compile(
    r"(?:^\s*[\d,]+\+?\s+.*\bjobs?\b|\bjobs?\s+in\b)",
    re.IGNORECASE,
)
_COLLECTION_PATH = re.compile(
    r"/(?:search|zhaopin|topics?|job-search|jobs/search|q-[^/]+)(?:/|$|\.html)",
    re.IGNORECASE,
)
_SPAM_TITLE = re.compile(
    r"(?:微信|电话|外围|工作室|联系(?:电话|方式)?|\{[^}\r\n]{1,80}\}|(?:\d[\s-]*){8,})",
    re.IGNORECASE,
)
_PAGINATION_QUERY_KEYS = frozenset({"page", "pagenum", "position", "start"})
_SEARCH_QUERY_KEYS = frozenset(
    {"q", "query", "keyword", "keywords", "location", "l"}
)
_SOCIAL_PATH = re.compile(r"/(?:posts?|status|feed)(?:/|$)", re.IGNORECASE)
_SOCIAL_RESULT_TYPES = frozenset(
    {"social", "social_post", "short_form", "community_post"}
)
_GENERIC_CAREERS = re.compile(r"^(?:careers?|jobs?|join us)(?:\s*[-|].*)?$", re.IGNORECASE)
_UNAVAILABLE_SIGNAL = re.compile(
    r"\b(?:404|not found|expired|unavailable|access denied|forbidden|error)\b",
    re.IGNORECASE,
)


class CandidateAssessment(BaseModel):
    model_config = ConfigDict(frozen=True)

    page_kind: CandidatePageKind
    score: float
    reason_codes: tuple[str, ...] = ()


class JobCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: str
    title: str
    company: str = ""
    location: str = ""
    snippet: str = ""
    source: str = ""
    retrieved_at: str = ""
    url_kind: UrlKind
    confidence: float
    provider_position: int
    page_kind: CandidatePageKind = "unknown_candidate"
    score: float = 0.0
    reason_codes: tuple[str, ...] = ()


def rank_job_candidates(
    results: Sequence[Mapping[str, Any]],
    *,
    limit: int,
) -> tuple[JobCandidate, ...]:
    ordered = sorted(
        results,
        key=lambda item: (
            _KIND_PRIORITY.get(str(item.get("url_kind")), (99, 0.0))[0],
            _is_probable_collection(item),
            int(item.get("provider_position", 0)),
        ),
    )
    ranked: list[JobCandidate] = []
    seen: set[str] = set()
    for item in ordered:
        kind = str(item.get("url_kind"))
        if kind not in _KIND_PRIORITY:
            continue
        url = _canonical_http_url(str(item.get("url") or ""))
        title = " ".join(str(item.get("title") or "").split())
        if not url or not title or url in seen:
            continue
        assessment = assess_job_candidate(item, url=url, title=title)
        if assessment.page_kind in {
            "collection_page",
            "social_or_content_page",
            "invalid_or_unsafe_url",
        }:
            continue
        seen.add(url)
        ranked.append(
            JobCandidate(
                url=url,
                title=title,
                company=str(item.get("company") or ""),
                location=str(item.get("location") or ""),
                snippet=str(item.get("snippet") or "")[:1000],
                source=str(item.get("source") or ""),
                retrieved_at=str(item.get("retrieved_at") or ""),
                url_kind=kind,
                confidence=_KIND_PRIORITY[kind][1],
                provider_position=int(item.get("provider_position", 0)),
                page_kind=assessment.page_kind,
                score=assessment.score,
                reason_codes=assessment.reason_codes,
            )
        )
    ranked.sort(
        key=lambda item: (
            0 if item.page_kind == "job_detail_candidate" else 1,
            _KIND_PRIORITY[item.url_kind][0],
            -item.score,
            item.provider_position,
        )
    )
    groups: dict[tuple[str, ...], list[JobCandidate]] = {}
    for candidate in ranked:
        groups.setdefault(_job_identity(candidate), []).append(candidate)
    distinct_first = [items[0] for items in groups.values()]
    mirrors = [item for items in groups.values() for item in items[1:]]
    return tuple((*distinct_first, *mirrors)[:limit])


def _is_probable_collection(item: Mapping[str, Any]) -> bool:
    return bool(_COLLECTION_TITLE.search(str(item.get("title") or "")))


def _is_unusable_candidate(
    item: Mapping[str, Any],
    *,
    url: str,
    title: str,
) -> bool:
    return assess_job_candidate(item, url=url, title=title).page_kind in {
        "collection_page",
        "social_or_content_page",
        "invalid_or_unsafe_url",
    }


def assess_job_candidate(
    item: Mapping[str, Any],
    *,
    url: str,
    title: str,
) -> CandidateAssessment:
    parsed = urlsplit(url)
    path = parsed.path.casefold()
    query_keys = {key.casefold() for key, _value in parse_qsl(parsed.query)}
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        return CandidateAssessment(
            page_kind="invalid_or_unsafe_url",
            score=0.0,
            reason_codes=("invalid_public_url",),
        )
    if _SPAM_TITLE.search(title):
        return CandidateAssessment(
            page_kind="social_or_content_page",
            score=0.0,
            reason_codes=("spam_title",),
        )
    if (
        str(item.get("result_type") or "").casefold()
        in _SOCIAL_RESULT_TYPES
        or _SOCIAL_PATH.search(path)
    ):
        return CandidateAssessment(
            page_kind="social_or_content_page",
            score=0.0,
            reason_codes=("social_or_content_shape",),
        )
    if (
        _is_probable_collection(item)
        or _COLLECTION_PATH.search(path)
        or any(
            segment.endswith(("topic", "topics"))
            for segment in path.rstrip("/").rsplit("/", 2)
        )
        or (
            path.startswith("/jobs/")
            and path.rstrip("/").endswith("-jobs")
            and bool(query_keys & _PAGINATION_QUERY_KEYS)
        )
        or (
            path.rstrip("/") in {"/jobs", "/m/jobs"}
            and bool(query_keys & _SEARCH_QUERY_KEYS)
        )
    ):
        return CandidateAssessment(
            page_kind="collection_page",
            score=0.0,
            reason_codes=("collection_page_shape",),
        )

    kind = str(item.get("url_kind") or "")
    reasons: list[str] = []
    score = _KIND_PRIORITY.get(kind, (99, 0.0))[1]
    if kind in {"structured_apply", "structured_share"}:
        reasons.append("structured_job_link")
        page_kind: CandidatePageKind = "job_detail_candidate"
    elif item.get("company") or item.get("location"):
        reasons.append("job_metadata_present")
        page_kind = "job_detail_candidate"
    else:
        reasons.append("needs_browser_classification")
        page_kind = "unknown_candidate"
    haystack = f"{title} {item.get('snippet') or ''}"
    if _GENERIC_CAREERS.match(title):
        score -= 0.3
        reasons.append("generic_careers_signal")
    if _UNAVAILABLE_SIGNAL.search(haystack):
        score -= 0.5
        reasons.append("unavailable_page_signal")
    if item.get("company"):
        score += 0.05
    if item.get("location"):
        score += 0.03
    if item.get("snippet"):
        score += 0.02
    return CandidateAssessment(
        page_kind=page_kind,
        score=max(0.0, min(1.0, score)),
        reason_codes=tuple(reasons),
    )


def _job_identity(candidate: JobCandidate) -> tuple[str, ...]:
    title = " ".join(candidate.title.casefold().split())
    company = " ".join(candidate.company.casefold().split())
    location = " ".join(candidate.location.casefold().split())
    if company or location:
        return title, company, location
    return title, urlsplit(candidate.url).hostname or ""


def _canonical_http_url(value: str) -> str:
    value = value.replace("\\u003d", "=").replace("\\u0026", "&")
    try:
        parsed = urlsplit(value)
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
            return ""
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError:
        return ""
    return urlunsplit(
        (
            parsed.scheme.casefold(),
            f"{parsed.hostname.casefold()}{port}",
            parsed.path or "/",
            parsed.query,
            "",
        )
    )
