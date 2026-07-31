from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


CandidateEvidenceLevel = Literal["complete", "partial"]
CandidateSelectionStatus = Literal[
    "PENDING_CONFIRMATION",
    "SELECTED",
    "EXPIRED",
]


class JobSelectionReference(BaseModel):
    model_config = ConfigDict(frozen=True)

    ordinal: int | None = Field(default=None, ge=1, le=100)
    candidate_id: UUID | None = None


class PendingJobCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate_id: UUID
    session_id: UUID
    turn_id: UUID
    ordinal: int = Field(ge=1)
    title: str
    company: str = ""
    location: str = ""
    source_url: str = ""
    evidence_level: CandidateEvidenceLevel
    status: CandidateSelectionStatus
    payload: dict[str, Any]
    created_at: datetime
    expires_at: datetime


_UUID = re.compile(
    r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b"
)
_ARABIC_SELECTIONS = (
    re.compile(r"(?i)\bCandidate\s*#?\s*(?P<value>\d{1,2})\b"),
    re.compile(r"(?:选择|选)\s*(?:第\s*)?(?P<value>\d{1,2})(?:\s*(?:个|号|项|岗位|职位))?"),
    re.compile(r"^\s*第\s*(?P<value>\d{1,2})\s*个(?:\s*(?:岗位|职位))?(?:\s*.*)?$"),
)
_CHINESE_SELECTION = re.compile(
    r"(?:选择|选)?\s*第\s*(?P<value>十|[一二三四五六七八九])\s*个?\s*(?:岗位|职位|Candidate)",
    re.IGNORECASE,
)
_CHINESE_ORDINALS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def parse_job_selection_reference(message: str) -> JobSelectionReference | None:
    normalized = " ".join(message.strip().split())
    if not normalized:
        return None
    candidate_id = _UUID.search(normalized)
    if candidate_id is not None:
        return JobSelectionReference(candidate_id=UUID(candidate_id.group(0)))
    chinese = _CHINESE_SELECTION.search(normalized)
    if chinese is not None:
        return JobSelectionReference(
            ordinal=_CHINESE_ORDINALS[chinese.group("value")]
        )
    for pattern in _ARABIC_SELECTIONS:
        found = pattern.search(normalized)
        if found is not None:
            value = int(found.group("value"))
            if value > 0:
                return JobSelectionReference(ordinal=value)
    return None
