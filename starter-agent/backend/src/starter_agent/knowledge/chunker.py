from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID, uuid5

from starter_agent.knowledge.errors import KnowledgeError
from starter_agent.knowledge.models import (
    KnowledgeChunk,
    KnowledgeScope,
    ParsedBlock,
    ParsedDocument,
)


class KnowledgeChunker:
    def __init__(
        self,
        *,
        target_chars: int = 1200,
        max_chars: int = 1800,
        overlap_chars: int = 150,
    ) -> None:
        self.target_chars = target_chars
        self.max_chars = max(max_chars, target_chars)
        self.overlap_chars = min(overlap_chars, target_chars - 1)

    def chunk(
        self,
        *,
        parsed: ParsedDocument,
        scope: KnowledgeScope,
        knowledge_base_id: UUID,
        document_id: UUID,
        version_id: UUID,
        version: int,
        filename: str,
    ) -> list[KnowledgeChunk]:
        groups: list[list[ParsedBlock]] = []
        current: list[ParsedBlock] = []
        current_length = 0
        for block in parsed.blocks:
            pieces = self._split_block(block)
            for piece in pieces:
                projected = current_length + (2 if current else 0) + len(piece.text)
                section_changed = bool(
                    current and current[-1].section_path != piece.section_path
                )
                if current and (
                    projected > self.target_chars
                    or section_changed
                ):
                    groups.append(current)
                    current = []
                    current_length = 0
                current.append(piece)
                current_length += (2 if current_length else 0) + len(piece.text)
        if current:
            groups.append(current)
        if not groups:
            raise KnowledgeError("document_no_indexable_content")

        created_at = datetime.now(UTC)
        result: list[KnowledgeChunk] = []
        for ordinal, group in enumerate(groups):
            text = "\n\n".join(item.text for item in group)
            digest = sha256(text.encode("utf-8")).hexdigest()
            result.append(
                KnowledgeChunk(
                    id=uuid5(version_id, f"{ordinal}:{digest}"),
                    document_id=document_id,
                    version_id=version_id,
                    knowledge_base_id=knowledge_base_id,
                    user_id=scope.user_id,
                    project_id=scope.project_id,
                    version=version,
                    filename=filename,
                    section_path=group[0].section_path,
                    start_line=min(item.start_line for item in group),
                    end_line=max(item.end_line for item in group),
                    ordinal=ordinal,
                    text=text,
                    search_text=" ".join(item.search_text for item in group),
                    content_sha256=digest,
                    created_at=created_at,
                )
            )
        return result

    def _split_block(self, block: ParsedBlock) -> list[ParsedBlock]:
        if len(block.text) <= self.max_chars or block.kind in {"code", "table", "list"}:
            return [block]
        pieces: list[ParsedBlock] = []
        start = 0
        while start < len(block.text):
            end = min(start + self.max_chars, len(block.text))
            if end < len(block.text):
                boundary = max(
                    block.text.rfind(marker, start, end)
                    for marker in ("。", "！", "？", ". ", "\n", " ")
                )
                if boundary > start + self.target_chars // 2:
                    end = boundary + 1
            value = block.text[start:end].strip()
            if value:
                pieces.append(
                    block.model_copy(
                        update={"text": value, "search_text": " ".join(value.casefold().split())}
                    )
                )
            if end >= len(block.text):
                break
            start = max(end - self.overlap_chars, start + 1)
        return pieces
