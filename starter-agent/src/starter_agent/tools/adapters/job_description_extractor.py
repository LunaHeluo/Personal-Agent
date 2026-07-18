from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from bs4 import BeautifulSoup, NavigableString, Tag


@dataclass(frozen=True)
class ExtractedJobDescription:
    title: str = ""
    company: str = ""
    location: str = ""
    employment_type: str = ""
    salary: str | None = None
    responsibilities: list[str] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)
    preferred_qualifications: list[str] = field(default_factory=list)
    benefits: list[str] = field(default_factory=list)
    raw_text: str = ""
    completeness: Literal["complete", "partial", "unverified"] = "unverified"
    extraction_method: Literal["json_ld", "html", "plain_text"] = "html"


class JobDescriptionExtractor:
    """Extract job-description fields; fetched content remains inert data."""

    SECTION_NAMES = {
        "responsibilities": (
            "responsibilities",
            "what you'll do",
            "岗位职责",
            "工作职责",
        ),
        "requirements": (
            "requirements",
            "qualifications",
            "任职要求",
            "职位要求",
        ),
        "preferred_qualifications": (
            "preferred qualifications",
            "nice to have",
            "加分项",
        ),
        "benefits": ("benefits", "what we offer", "福利"),
    }

    _NOISE_TAGS = ("script", "style", "nav", "footer", "aside", "form")
    _NOISE_CLASS = re.compile(r"cookie|banner|modal", re.IGNORECASE)
    _HEADING_NAME = re.compile(r"^h[1-6]$")
    _BULLET_PREFIX = re.compile(r"^(?:[-*•‣]|\d+[.)])\s*")
    _CONTENT_BLOCK_NAMES = {"div", "section", "article", "dd", "td"}
    _MAX_JSON_LD_NODES = 10_000
    _MAX_JSON_LD_DEPTH = 64

    def extract(
        self, content: str, content_type: str
    ) -> ExtractedJobDescription:
        if content_type.lower().startswith("text/plain"):
            return self._from_plain_text(content)

        soup = BeautifulSoup(content, "html.parser")
        structured = self._job_posting_json_ld(soup)
        if structured is not None:
            return self._from_json_ld(structured)
        return self._from_html(soup)

    def _job_posting_json_ld(self, soup: BeautifulSoup) -> dict[str, Any] | None:
        for script in soup.find_all("script", type=re.compile("ld\\+json", re.I)):
            raw_json = script.string or script.get_text()
            if not raw_json or not raw_json.strip():
                continue
            try:
                payload = json.loads(raw_json)
            except (RecursionError, TypeError, ValueError):
                continue
            for item in self._json_ld_items(payload):
                item_type = item.get("@type")
                type_values = item_type if isinstance(item_type, list) else [item_type]
                if any(
                    isinstance(value, str) and value.lower() == "jobposting"
                    for value in type_values
                ):
                    return item
        return None

    def _json_ld_items(self, payload: object) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        stack: list[tuple[object, int]] = [(payload, 0)]
        visited = 0
        while stack and visited < self._MAX_JSON_LD_NODES:
            node, depth = stack.pop()
            visited += 1
            if depth > self._MAX_JSON_LD_DEPTH:
                continue
            if isinstance(node, list):
                stack.extend((item, depth + 1) for item in reversed(node))
                continue
            if not isinstance(node, dict):
                continue
            items.append(node)
            graph = node.get("@graph")
            if isinstance(graph, list):
                stack.extend((item, depth + 1) for item in reversed(graph))
            elif isinstance(graph, dict):
                stack.append((graph, depth + 1))
        return items

    def _from_json_ld(self, posting: dict[str, Any]) -> ExtractedJobDescription:
        description = self._string_value(posting.get("description"))
        description_soup = BeautifulSoup(description, "html.parser")
        sections = self._split_sections_from_html(description_soup)
        raw_text = self._normalise_text(description_soup.get_text(" ", strip=True))
        salary = self._salary_value(posting.get("baseSalary"))
        return ExtractedJobDescription(
            title=self._string_value(posting.get("title")),
            company=self._organization_name(posting.get("hiringOrganization")),
            location=self._location_value(posting.get("jobLocation")),
            employment_type=self._joined_value(posting.get("employmentType")),
            salary=salary,
            raw_text=raw_text,
            completeness=self._completeness(
                sections["responsibilities"], sections["requirements"]
            ),
            extraction_method="json_ld",
            **sections,
        )

    def _from_html(self, soup: BeautifulSoup) -> ExtractedJobDescription:
        for element in soup.find_all(self._NOISE_TAGS):
            element.decompose()
        for element in soup.find_all(class_=self._NOISE_CLASS):
            element.decompose()

        content_root = soup.find("main") or soup.find("article") or soup.body or soup
        sections = self._split_sections_from_html(content_root)
        raw_text = self._normalise_text(content_root.get_text(" ", strip=True))
        title_tag = content_root.find("h1")
        company_tag = content_root.find(
            class_=re.compile(r"company|employer|organization", re.I)
        )
        return ExtractedJobDescription(
            title=self._text_from_tag(title_tag),
            company=self._text_from_tag(company_tag),
            raw_text=raw_text,
            completeness=self._completeness(
                sections["responsibilities"], sections["requirements"]
            ),
            extraction_method="html",
            **sections,
        )

    def _from_plain_text(self, content: str) -> ExtractedJobDescription:
        lines = [self._normalise_text(line) for line in content.splitlines()]
        lines = [line for line in lines if line]
        sections = self._split_sections_from_lines(lines)
        title = next(
            (line for line in lines if self._section_name(line) is None), ""
        )
        raw_text = "\n".join(lines)
        return ExtractedJobDescription(
            title=title,
            raw_text=raw_text,
            completeness=self._completeness(
                sections["responsibilities"], sections["requirements"]
            ),
            extraction_method="plain_text",
            **sections,
        )

    def _split_sections_from_html(
        self, soup: Tag | BeautifulSoup
    ) -> dict[str, list[str]]:
        sections = self._empty_sections()
        current_section: str | None = None
        for node in soup.descendants:
            if isinstance(node, Tag) and self._HEADING_NAME.match(node.name or ""):
                current_section = self._section_name(node.get_text(" ", strip=True))
                continue
            if current_section is None:
                continue
            if isinstance(node, Tag):
                if node.name in {"p", "li"} and not self._inside_text_item(node):
                    sections[current_section].append(node.get_text(" ", strip=True))
                elif (
                    node.name in self._CONTENT_BLOCK_NAMES
                    and not self._contains_semantic_content(node)
                    and not self._inside_text_item(node)
                ):
                    sections[current_section].append(node.get_text(" ", strip=True))
            elif isinstance(node, NavigableString) and self._is_bare_section_text(node):
                sections[current_section].append(str(node))
        return {name: self._clean_items(values) for name, values in sections.items()}

    def _split_sections_from_lines(self, lines: list[str]) -> dict[str, list[str]]:
        sections = self._empty_sections()
        current: str | None = None
        for line in lines:
            section_name = self._section_name(line)
            if section_name is not None:
                current = section_name
                continue
            if current is not None:
                sections[current].append(line)
        return {name: self._clean_items(values) for name, values in sections.items()}

    def _contains_semantic_content(self, node: Tag) -> bool:
        return bool(node.find(self._HEADING_NAME) or node.find(["p", "li"]))

    @staticmethod
    def _inside_text_item(node: Tag) -> bool:
        return any(
            isinstance(parent, Tag) and parent.name in {"p", "li"}
            for parent in node.parents
        )

    def _is_bare_section_text(self, node: NavigableString) -> bool:
        parent = node.parent
        if not isinstance(parent, Tag) or parent.name not in {"body", "main", "article"}:
            return False
        return not any(
            isinstance(ancestor, Tag)
            and (self._HEADING_NAME.match(ancestor.name or "") or ancestor.name in {"p", "li"})
            for ancestor in node.parents
        )

    def _section_name(self, value: str) -> str | None:
        normalized = self._normalise_text(value).rstrip(":：").lower()
        for section_name, aliases in self.SECTION_NAMES.items():
            if normalized in aliases:
                return section_name
        return None

    @staticmethod
    def _empty_sections() -> dict[str, list[str]]:
        return {
            "responsibilities": [],
            "requirements": [],
            "preferred_qualifications": [],
            "benefits": [],
        }

    def _clean_items(self, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = self._BULLET_PREFIX.sub("", self._normalise_text(value))
            if not item or item in seen:
                continue
            cleaned.append(item)
            seen.add(item)
        return cleaned

    @staticmethod
    def _normalise_text(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _text_from_tag(tag: Tag | None) -> str:
        return tag.get_text(" ", strip=True) if tag else ""

    @staticmethod
    def _string_value(value: object) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        return ""

    def _organization_name(self, value: object) -> str:
        if isinstance(value, dict):
            return self._named_value(value)
        return self._named_value(value)

    def _joined_value(self, value: object) -> str:
        values = value if isinstance(value, list) else [value]
        return ", ".join(
            self._clean_items([self._named_value(item) for item in values])
        )

    def _named_value(self, value: object) -> str:
        if isinstance(value, dict):
            return self._string_value(value.get("name") or value.get("value"))
        return self._string_value(value)

    def _location_value(self, value: object) -> str:
        locations = value if isinstance(value, list) else [value]
        parts: list[str] = []
        for location in locations:
            if not isinstance(location, dict):
                continue
            address = location.get("address")
            if not isinstance(address, dict):
                continue
            pieces = [
                self._named_value(address.get(key))
                for key in ("addressLocality", "addressRegion", "addressCountry")
            ]
            formatted = ", ".join(piece for piece in pieces if piece)
            if formatted:
                parts.append(formatted)
        return "; ".join(self._clean_items(parts))

    def _salary_value(self, value: object) -> str | None:
        if isinstance(value, dict):
            amount = value.get("value")
            currency = self._named_value(value.get("currency"))
            unit = self._named_value(value.get("unitText"))
            if isinstance(amount, dict):
                lower = self._string_value(amount.get("minValue"))
                upper = self._string_value(amount.get("maxValue"))
                fixed = self._string_value(amount.get("value"))
                amount_value = " - ".join(part for part in (lower, upper) if part)
                amount_value = amount_value or fixed
                unit = self._named_value(amount.get("unitText")) or unit
                return " ".join(
                    part for part in (currency, amount_value, unit) if part
                ) or None
            amount_value = self._string_value(amount)
            return " ".join(part for part in (currency, amount_value, unit) if part) or None
        return self._string_value(value) or None

    @staticmethod
    def _completeness(
        responsibilities: list[str], requirements: list[str]
    ) -> Literal["complete", "partial", "unverified"]:
        if responsibilities and requirements:
            return "complete"
        if responsibilities or requirements:
            return "partial"
        return "unverified"
