from __future__ import annotations

import re

from pydantic import BaseModel

from starter_agent.knowledge.models import Evidence


class EvidenceDecision(BaseModel):
    allowed: bool
    reason: str | None = None
    conflict: bool = False


class EvidenceSufficiencyGate:
    def evaluate(
        self, question: str, evidence: list[Evidence]
    ) -> EvidenceDecision:
        if not evidence:
            return EvidenceDecision(allowed=False, reason="no_evidence")
        corpus = "\n".join(item.text for item in evidence).casefold()
        required_numbers = set(re.findall(r"\d+(?:\.\d+)?%?", question))
        if any(value not in corpus for value in required_numbers):
            return EvidenceDecision(
                allowed=False, reason="insufficient_evidence"
            )
        required_entities = {
            value.casefold()
            for value in re.findall(
                r"\b[A-Z][A-Za-z0-9._+-]{2,}\b|[\w.+-]+@[\w.-]+",
                question,
            )
            if value.casefold() not in {"hr"}
        }
        if any(value not in corpus for value in required_entities):
            return EvidenceDecision(
                allowed=False, reason="insufficient_evidence"
            )
        values_by_document = [
            set(re.findall(r"\d+(?:\.\d+)?%?", item.text))
            for item in evidence
        ]
        non_empty = [values for values in values_by_document if values]
        conflict = (
            len(non_empty) >= 2
            and len(set().union(*non_empty)) > 1
            and any(
                token in question
                for token in ("多少", "比例", "百分比", "数字")
            )
        )
        return EvidenceDecision(allowed=True, conflict=conflict)
