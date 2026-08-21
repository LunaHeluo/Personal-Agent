from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from starter_agent.job_research.candidates import JobCandidate
from starter_agent.job_research.company_attribution import (
    preferred_company_attribution,
)
from starter_agent.tools.adapters.job_description_extractor import JobDescriptionExtractor
from starter_agent.tools.adapters.safe_web_fetcher import FetchFailure, SafeWebFetcher


@dataclass(frozen=True)
class FallbackFailure:
    error_code: str
    safe_reason: str


@dataclass(frozen=True)
class FallbackResult:
    jobs: tuple[dict[str, Any], ...] = ()
    partial_jobs: tuple[dict[str, Any], ...] = ()
    method: str = "none"
    failures: tuple[FallbackFailure, ...] = ()


class JobPageFallback:
    def __init__(self, fetcher: SafeWebFetcher, extractor: JobDescriptionExtractor | None = None) -> None:
        self.fetcher = fetcher
        self.extractor = extractor or JobDescriptionExtractor()

    async def retrieve(self, candidate: JobCandidate) -> FallbackResult:
        failures: tuple[FallbackFailure, ...] = ()
        try:
            page = await self.fetcher.fetch(candidate.url)
            extracted = self.extractor.extract(page.text, page.content_type)
            validation_state = extracted.validation_state
            if validation_state == "rejected" and extracted.title:
                validation_state = (
                    "verified" if extracted.completeness == "complete" else
                    "partial_verified" if extracted.completeness == "partial" else
                    "rejected"
                )
            if validation_state != "rejected":
                method = "http_json_ld" if extracted.extraction_method == "json_ld" else "http_html"
                payload = asdict(extracted)
                page_attribution = {
                    "company": extracted.company,
                    "company_source": (
                        "page_json_ld"
                        if extracted.company and extracted.extraction_method == "json_ld"
                        else "page_html" if extracted.company else ""
                    ),
                    "company_confidence": "high" if extracted.company else "",
                }
                attribution = preferred_company_attribution(
                    page_attribution,
                    {
                        "company": candidate.company,
                        "company_source": candidate.company_source,
                        "company_confidence": candidate.company_confidence,
                    },
                )
                payload.update(
                    company=attribution.company,
                    company_source=attribution.source,
                    company_confidence=attribution.confidence,
                    source_url=page.final_url,
                    retrieved_at=candidate.retrieved_at,
                    retrieval_method=method,
                    page_type="job_detail",
                    validation_state=validation_state,
                )
                if validation_state == "verified":
                    return FallbackResult(jobs=(payload,), method=method)
                return FallbackResult(partial_jobs=(payload,), method=method)
            failures = (
                FallbackFailure(
                    "access_blocked_challenge",
                    "HTTP page returned an access challenge",
                ),
            ) if extracted.page_type == "error" else (
                FallbackFailure(
                    "selector_unmatched",
                    "HTTP page did not contain a verifiable job description",
                ),
            )
        except FetchFailure as exc:
            failures = (FallbackFailure(exc.code, exc.display),)

        snippet = " ".join(candidate.snippet.split())
        if not snippet:
            return FallbackResult(failures=failures)
        partial = {
            "title": candidate.title,
            "company": candidate.company,
            "company_source": candidate.company_source,
            "company_confidence": candidate.company_confidence,
            "location": candidate.location,
            "responsibilities": [],
            "requirements": [],
            "raw_text": snippet,
            "source_url": candidate.url,
            "retrieved_at": candidate.retrieved_at,
            "page_type": "job_description",
            "validation_state": "partial_verified",
            "retrieval_method": "search_snippet",
            "validation_reason_codes": ["search_evidence_only"],
        }
        return FallbackResult(partial_jobs=(partial,), method="search_snippet", failures=failures)
