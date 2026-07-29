from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict

from starter_agent.knowledge.models import RetrievalMatch


DecisionReason = Literal[
    "matched",
    "missing_jd",
    "location_mismatch",
    "role_mismatch",
    "closed",
    "expired",
    "explicit_freshness",
]

_STATUS = re.compile(r"(?im)^-\s*Status:\s*(?P<value>[^\r\n]+)")
_CLOSING_DATE = re.compile(
    r"(?im)^-\s*Closing Date:\s*(?P<value>\d{4}-\d{2}-\d{2})\s*$"
)
_ROLE_TOKEN = re.compile(r"[\w+#.-]+", re.UNICODE)
_KNOWLEDGE_SOURCE = re.compile(r"知识库|knowledge\s*base", re.IGNORECASE)
_WEB_SOURCE = re.compile(
    r"联网|网上|互联网|公开网|最新|当前招聘|\b(?:web|online|internet)\b",
    re.IGNORECASE,
)
_ROLE_ALIASES = {
    "developer": "engineer",
    "development": "engineer",
    "engineering": "engineer",
}
_CLOSED_STATUSES = frozenset(
    {"closed", "expired", "inactive", "filled", "cancelled", "canceled"}
)


class JobResearchCriteria(BaseModel):
    model_config = ConfigDict(frozen=True)

    location: str | None = None
    role_terms: tuple[str, ...] = ()
    explicit_freshness: bool = False


class KnowledgeJobDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    use_knowledge: bool
    reason_code: DecisionReason
    matches: tuple[RetrievalMatch, ...] = ()


class KnowledgeJobMatcher:
    def evaluate(
        self,
        *,
        criteria: JobResearchCriteria,
        matches: tuple[RetrievalMatch, ...],
        now: datetime,
        freshness_days: int,
    ) -> KnowledgeJobDecision:
        candidates = tuple(
            item
            for item in matches
            if item.document_type == "job_description"
        )
        if not candidates:
            return self._fallback("missing_jd")

        within_closing_date = tuple(
            item for item in candidates if not self._is_past_closing_date(item, now)
        )
        if not within_closing_date:
            return self._fallback("expired")

        if criteria.explicit_freshness:
            return self._fallback("explicit_freshness")

        active = tuple(
            item for item in within_closing_date if not self._is_closed(item)
        )
        if not active:
            return self._fallback("closed")

        fresh = tuple(
            item
            for item in active
            if self._is_fresh(
                item.created_at,
                now=now,
                freshness_days=freshness_days,
            )
        )
        if not fresh:
            return self._fallback("expired")

        location = self._normalize(criteria.location)
        located = tuple(
            item
            for item in fresh
            if not location or location in self._normalize(item.preview)
        )
        if not located:
            return self._fallback("location_mismatch")

        terms = self._role_tokens(" ".join(criteria.role_terms))
        relevant = tuple(
            item
            for item in located
            if not terms
            or self._role_matches(terms, item.preview)
        )
        if not relevant:
            return self._fallback("role_mismatch")
        return KnowledgeJobDecision(
            use_knowledge=True,
            reason_code="matched",
            matches=relevant,
        )

    @staticmethod
    def _fallback(reason: DecisionReason) -> KnowledgeJobDecision:
        return KnowledgeJobDecision(use_knowledge=False, reason_code=reason)

    @staticmethod
    def _normalize(value: str | None) -> str:
        return " ".join((value or "").casefold().split())

    @staticmethod
    def _is_closed(match: RetrievalMatch) -> bool:
        found = _STATUS.search(match.preview)
        if found is None:
            return False
        return found.group("value").strip().casefold() in _CLOSED_STATUSES

    @staticmethod
    def _is_past_closing_date(match: RetrievalMatch, now: datetime) -> bool:
        found = _CLOSING_DATE.search(match.preview)
        if found is None:
            return False
        try:
            closing_date = date.fromisoformat(found.group("value"))
        except ValueError:
            return False
        current = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
        return closing_date < current.date()

    @staticmethod
    def _role_tokens(value: str) -> tuple[str, ...]:
        return tuple(
            _ROLE_ALIASES.get(token, token)
            for token in (
                match.group(0).casefold() for match in _ROLE_TOKEN.finditer(value)
            )
        )

    @classmethod
    def _role_matches(cls, required: tuple[str, ...], preview: str) -> bool:
        preview_tokens = tuple(
            _ROLE_ALIASES.get(token, token)
            for token in (
                match.group(0).casefold() for match in _ROLE_TOKEN.finditer(preview)
            )
        )
        available = frozenset(preview_tokens)
        if frozenset(required).issubset(available):
            return True
        if len(required) < 2:
            return False
        requested_pairs = set(zip(required, required[1:], strict=False))
        preview_pairs = set(zip(preview_tokens, preview_tokens[1:], strict=False))
        return bool(requested_pairs & preview_pairs)

    @staticmethod
    def _is_fresh(
        created_at: datetime | None,
        *,
        now: datetime,
        freshness_days: int,
    ) -> bool:
        if created_at is None:
            return False
        timestamp = (
            created_at.replace(tzinfo=UTC)
            if created_at.tzinfo is None
            else created_at.astimezone(UTC)
        )
        current = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
        return current - timestamp <= timedelta(days=freshness_days)


def requests_knowledge_only(value: str) -> bool:
    """Return whether the user explicitly scopes this request to stored knowledge."""

    return bool(_KNOWLEDGE_SOURCE.search(value)) and not bool(
        _WEB_SOURCE.search(value)
    )
