"""Conservative, editable structural projection for a job description."""

from __future__ import annotations

import re
from typing import Any


_HEADINGS = {
    "responsibilities": ("岗位职责", "工作职责", "职责", "responsibilities", "what you will do"),
    "required_skills": ("任职要求", "岗位要求", "职位要求", "基本要求", "requirements", "qualifications", "must have"),
    "preferred_skills": ("加分项", "优先条件", "优先", "preferred", "nice to have", "bonus"),
}


def extract_job_analysis(markdown: str) -> dict[str, Any]:
    """Extract only explicit JD statements; callers may edit every result."""
    sections = {key: [] for key in _HEADINGS}
    current: str | None = None
    for raw in markdown.splitlines():
        line = _clean(raw)
        heading = _heading(line)
        if heading:
            current = heading
            continue
        if _looks_like_heading(raw, line):
            current = None
            continue
        if current is not None and line:
            sections[current].append(line)

    if not any(sections.values()):
        for raw in markdown.splitlines():
            line = _clean(raw)
            if len(line) < 4:
                continue
            folded = line.casefold()
            target = (
                "preferred_skills" if any(word in folded for word in ("优先", "加分", "preferred", "plus"))
                else "responsibilities" if any(word in folded for word in ("职责", "负责", "responsib"))
                else "required_skills"
            )
            sections[target].append(line)
    return {key: _unique(values)[:30] for key, values in sections.items()}


def _clean(line: str) -> str:
    value = re.sub(r"^\s*(?:#{1,6}\s*|[-*+•]\s*|\d+[.)、]\s*)", "", line).strip()
    return re.sub(r"[*_`]", "", value).strip()


def _heading(line: str) -> str | None:
    normalized = re.sub(r"[\s:：()（）【】\[\]._\-]+", "", line).casefold()
    for name, aliases in _HEADINGS.items():
        if any(normalized == re.sub(r"\s+", "", alias).casefold() for alias in aliases):
            return name
    return None


def _looks_like_heading(raw: str, line: str) -> bool:
    if raw.strip().startswith("#"):
        return True
    return bool(re.fullmatch(r"[A-Z][A-Z\s&/\-]{1,79}", line))


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result
