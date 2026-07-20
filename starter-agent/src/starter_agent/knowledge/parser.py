from __future__ import annotations

import re

from starter_agent.knowledge.models import ParsedBlock, ParsedDocument


_ATX = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_SETEXT = re.compile(r"^\s*(=+|-+)\s*$")
_LIST = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+")


def _search_text(value: str) -> str:
    return " ".join(value.casefold().split())


class MarkdownParser:
    def parse(self, source: str) -> ParsedDocument:
        normalized = source.removeprefix("\ufeff").replace("\r\n", "\n").replace(
            "\r", "\n"
        )
        lines = normalized.splitlines()
        blocks: list[ParsedBlock] = []
        sections: list[str] = []
        index = 0
        while index < len(lines):
            line = lines[index]
            if not line.strip():
                index += 1
                continue
            atx = _ATX.match(line)
            if atx:
                self._set_section(sections, len(atx.group(1)), atx.group(2).strip())
                index += 1
                continue
            if index + 1 < len(lines) and line.strip() and _SETEXT.match(
                lines[index + 1]
            ):
                level = 1 if lines[index + 1].lstrip().startswith("=") else 2
                self._set_section(sections, level, line.strip())
                index += 2
                continue

            start = index
            if line.lstrip().startswith("```") or line.lstrip().startswith("~~~"):
                marker = line.lstrip()[:3]
                index += 1
                while index < len(lines):
                    if lines[index].lstrip().startswith(marker):
                        index += 1
                        break
                    index += 1
                kind = "code"
            elif _LIST.match(line):
                index += 1
                while index < len(lines) and (
                    _LIST.match(lines[index])
                    or (lines[index].startswith((" ", "\t")) and lines[index].strip())
                ):
                    index += 1
                kind = "list"
            elif line.lstrip().startswith("|"):
                index += 1
                while index < len(lines) and lines[index].lstrip().startswith("|"):
                    index += 1
                kind = "table"
            elif line.lstrip().startswith(">"):
                index += 1
                while index < len(lines) and lines[index].lstrip().startswith(">"):
                    index += 1
                kind = "quote"
            else:
                index += 1
                while index < len(lines):
                    candidate = lines[index]
                    if not candidate.strip():
                        break
                    if (
                        _ATX.match(candidate)
                        or _LIST.match(candidate)
                        or candidate.lstrip().startswith((">", "```", "~~~", "|"))
                    ):
                        break
                    index += 1
                kind = "paragraph"

            text = "\n".join(lines[start:index]).strip()
            if text:
                blocks.append(
                    ParsedBlock(
                        kind=kind,
                        text=text,
                        search_text=_search_text(text),
                        section_path=list(sections),
                        start_line=start + 1,
                        end_line=index,
                    )
                )
        return ParsedDocument(normalized_source=normalized, blocks=blocks)

    @staticmethod
    def _set_section(sections: list[str], level: int, title: str) -> None:
        del sections[level - 1 :]
        while len(sections) < level - 1:
            sections.append("")
        sections.append(title)
