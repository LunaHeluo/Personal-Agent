"""Deterministic structured profile extraction for imported resumes.

The workbench retains the original normalized Markdown as the authoritative
resume version.  This module derives a compact read model for UI statistics
and evidence-aware assistance without inventing experience from an LLM.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


_SECTION_ALIASES = {
    "summary": ("个人简介", "个人优势", "自我评价", "核心优势", "profile", "summary"),
    "education": ("教育经历", "教育背景", "教育", "education"),
    "experience": ("工作经历", "工作经验", "实习经历", "职业经历", "experience", "employment", "research experience", "professional experience", "internship"),
    "projects": ("项目经历", "项目经验", "项目", "projects", "project experience", "selected projects"),
    "skills": ("专业技能", "技能清单", "技能", "technical skills", "skills"),
}
_IGNORED_NAME_VALUES = ("简历", "resume", "个人信息", "联系方式", "教育经历", "工作经历", "实习经历", "项目经历", "技能")
_DATE_OR_SEPARATOR = re.compile(r"(?:19|20)\d{2}|[|｜]|\s(?:-|—|–)\s|\b(?:至今|present)\b", re.I)


def infer_resume_name(markdown: str, fallback_name: str = "") -> str:
    """Infer a short name only when the leading text looks safe to display."""
    fallback = Path(fallback_name).stem.strip() or "我的档案"
    for raw_line in markdown.splitlines()[:20]:
        line = _clean_line(raw_line)
        if _section_for_heading(line):
            break
        line = re.sub(r"^姓名\s*[:：]\s*", "", line).strip()
        if not (2 <= len(line) <= 40):
            continue
        compact = line.lower().replace(" ", "")
        if any(value in compact for value in _IGNORED_NAME_VALUES):
            continue
        if re.search(r"[@]|\d{7,}|[。；;，,、|｜]", line):
            continue
        return line
    return fallback[:200]


def extract_resume_profile(markdown: str, fallback_name: str = "") -> dict[str, Any]:
    """Return a conservative structured projection of a Markdown resume."""
    sections = _split_sections(markdown)
    skills = _extract_skills(sections["skills"])
    return {
        "schema_version": "resume-profile-v1",
        "name": infer_resume_name(markdown, fallback_name),
        "contact": _extract_contact(markdown),
        "summary": _first_text(sections["summary"]),
        "education": _extract_entries(sections["education"]),
        "experience": _extract_entries(sections["experience"]),
        "projects": _extract_entries(sections["projects"]),
        "skills": skills,
        "metrics": {
            "education": len(_extract_entries(sections["education"])),
            "experience": len(_extract_entries(sections["experience"])),
            "projects": len(_extract_entries(sections["projects"])),
            "skills": len(skills),
        },
    }


def _clean_line(line: str) -> str:
    value = re.sub(r"^\s*(?:#{1,6}\s*|[-*+]\s*|\d+[.)]\s*)", "", line).strip()
    return re.sub(r"[*_`]+", "", value).strip()


def _split_sections(markdown: str) -> dict[str, list[str]]:
    result = {name: [] for name in _SECTION_ALIASES}
    current: str | None = None
    for raw_line in markdown.splitlines():
        cleaned = _clean_line(raw_line)
        section = _section_for_heading(cleaned)
        if section:
            current = section
            continue
        if _looks_like_heading(raw_line, cleaned):
            # An unrecognised document heading must close the preceding
            # section. Otherwise every subsequent dated line is counted as,
            # for example, another education entry.
            current = None
            continue
        if current is not None:
            result[current].append(raw_line)
    return result


def _section_for_heading(line: str) -> str | None:
    normalized = re.sub(r"[\s:：()（）\[\]【】._-]+", "", line).lower()
    if not normalized or len(normalized) > 30:
        return None
    for section, aliases in _SECTION_ALIASES.items():
        if any(
            normalized == (alias_normalized := re.sub(r"\s+", "", alias).lower())
            or normalized.startswith(alias_normalized)
            for alias in aliases
        ):
            return section
    return None


def _looks_like_heading(raw_line: str, cleaned: str) -> bool:
    raw = raw_line.strip()
    if raw.startswith("#"):
        return True
    return bool(re.fullmatch(r"[A-Z][A-Z\s&/\-]{1,79}", cleaned))


def _extract_contact(markdown: str) -> dict[str, str]:
    text = "\n".join(markdown.splitlines()[:30])
    email = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, re.I)
    phone = re.search(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d[-\s]?\d{4}[-\s]?\d{4}(?!\d)", text)
    return {"email": email.group(0) if email else "", "phone": phone.group(0) if phone else ""}


def _extract_entries(lines: list[str]) -> list[dict[str, Any]]:
    cleaned = [_clean_line(line) for line in lines if _clean_line(line)]
    if not cleaned:
        return []
    headers = [index for index, line in enumerate(cleaned) if _DATE_OR_SEPARATOR.search(line)]
    if not headers:
        headers = [0]
        headers.extend(index for index, line in enumerate(cleaned[1:], start=1) if line.startswith(("•", "-")))
    headers = headers[:12]
    entries: list[dict[str, Any]] = []
    for position, start in enumerate(headers):
        end = headers[position + 1] if position + 1 < len(headers) else len(cleaned)
        title = cleaned[start][:240]
        details = [line[:500] for line in cleaned[start + 1 : end][:8]]
        entries.append({"title": title, "details": details})
    return entries


def _extract_skills(lines: list[str]) -> list[str]:
    seen: set[str] = set()
    values: list[str] = []
    for raw_line in lines:
        line = _clean_line(raw_line)
        for item in re.split(r"[,，、/|｜·;；\n]+", line):
            value = item.strip(" -•:：")
            if not value or len(value) > 60 or value.lower() in seen:
                continue
            seen.add(value.lower())
            values.append(value)
    return values[:60]


def _first_text(lines: list[str]) -> str:
    text = " ".join(_clean_line(line) for line in lines if _clean_line(line))
    return text[:1000]
