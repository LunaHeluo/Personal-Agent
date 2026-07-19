from __future__ import annotations

from uuid import UUID

from starter_agent.knowledge.index import SQLiteFtsIndex
from starter_agent.knowledge.models import (
    KnowledgeScope,
    RetrievalMatch,
)
from starter_agent.knowledge.query import normalize_query
from starter_agent.knowledge.store import SQLiteKnowledgeStore


class KnowledgeRetriever:
    def __init__(self, store: SQLiteKnowledgeStore):
        self.store = store
        self.index = SQLiteFtsIndex(store.engine)

    def retrieve(
        self,
        scope: KnowledgeScope,
        knowledge_base_id: UUID,
        question: str,
        *,
        top_k: int,
        document_ids: list[UUID] | None = None,
        document_types: list[str] | None = None,
        filenames: list[str] | None = None,
        versions: list[int] | None = None,
    ) -> list[RetrievalMatch]:
        query = normalize_query(question)
        if not query.match_expression:
            short_rows = self.store.search_short_terms(
                scope,
                knowledge_base_id,
                query.short_terms,
                limit=top_k,
                document_ids=document_ids,
                document_types=document_types,
                filenames=filenames,
                versions=versions,
            )
            return [
                RetrievalMatch(
                    chunk_id=chunk.id,
                    document_id=chunk.document_id,
                    document_type=document_type,
                    filename=chunk.filename,
                    version=chunk.version,
                    page=chunk.page,
                    section_path=chunk.section_path,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    preview=chunk.text[:800],
                    source_ref=chunk.source_ref,
                    bm25_score=None,
                    matched_terms=query.short_terms,
                    rank=rank,
                )
                for rank, (chunk, document_type) in enumerate(
                    short_rows, start=1
                )
            ]
        ranked = self.index.search(
            scope,
            knowledge_base_id,
            query.match_expression,
            limit=top_k,
            document_ids=document_ids,
            document_types=document_types,
            filenames=filenames,
            versions=versions,
        )
        chunks = self.store.get_chunks_by_ids(
            scope, knowledge_base_id, [item[0] for item in ranked]
        )
        matches: list[RetrievalMatch] = []
        for rank, (chunk_id, score) in enumerate(ranked, start=1):
            if chunk_id not in chunks:
                continue
            chunk, document_type = chunks[chunk_id]
            matched = [
                term
                for term in query.terms
                if term.casefold() in chunk.search_text
            ]
            matches.append(
                RetrievalMatch(
                    chunk_id=chunk.id,
                    document_id=chunk.document_id,
                    document_type=document_type,
                    filename=chunk.filename,
                    version=chunk.version,
                    page=chunk.page,
                    section_path=chunk.section_path,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    preview=chunk.text[:800],
                    source_ref=chunk.source_ref,
                    bm25_score=score,
                    matched_terms=matched,
                    rank=rank,
                )
            )
        return matches
