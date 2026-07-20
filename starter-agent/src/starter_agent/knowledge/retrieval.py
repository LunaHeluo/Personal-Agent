from __future__ import annotations

from uuid import UUID

from starter_agent.knowledge.index import SQLiteFtsIndex
from starter_agent.knowledge.mappings import QueryMappingCatalog
from starter_agent.knowledge.models import (
    KnowledgeChunk,
    KnowledgeScope,
    RetrievalMatch,
)
from starter_agent.knowledge.query import NormalizedQuery, normalize_query
from starter_agent.knowledge.store import SQLiteKnowledgeStore


class KnowledgeRetriever:
    def __init__(
        self,
        store: SQLiteKnowledgeStore,
        mapping_catalog: QueryMappingCatalog,
    ):
        self.store = store
        self.index = SQLiteFtsIndex(store.engine)
        self.mapping_catalog = mapping_catalog

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
        query = normalize_query(question, self.mapping_catalog)
        candidate_limit = min(max(top_k * 4, 20), 100)
        candidates: list[RetrievalMatch] = []

        if query.match_expression:
            candidates.extend(
                self._search_fts(
                    scope,
                    knowledge_base_id,
                    query,
                    limit=candidate_limit,
                    document_ids=document_ids,
                    document_types=document_types,
                    filenames=filenames,
                    versions=versions,
                )
            )
        if query.short_terms:
            candidates.extend(
                self._search_short_terms(
                    scope,
                    knowledge_base_id,
                    query,
                    limit=candidate_limit,
                    document_ids=document_ids,
                    document_types=document_types,
                    filenames=filenames,
                    versions=versions,
                )
            )

        candidates = self._deduplicate(candidates)
        if self._can_apply_comparison_coverage(
            query, top_k, document_types
        ):
            candidates = self._ensure_comparison_coverage(candidates)
        return [
            match.model_copy(update={"rank": rank})
            for rank, match in enumerate(candidates[:top_k], start=1)
        ]

    def _search_fts(
        self,
        scope: KnowledgeScope,
        knowledge_base_id: UUID,
        query: NormalizedQuery,
        *,
        limit: int,
        document_ids: list[UUID] | None,
        document_types: list[str] | None,
        filenames: list[str] | None,
        versions: list[int] | None,
    ) -> list[RetrievalMatch]:
        assert query.match_expression is not None
        ranked = self.index.search(
            scope,
            knowledge_base_id,
            query.match_expression,
            limit=limit,
            document_ids=document_ids,
            document_types=document_types,
            filenames=filenames,
            versions=versions,
        )
        chunks = self.store.get_chunks_by_ids(
            scope, knowledge_base_id, [item[0] for item in ranked]
        )
        matches: list[RetrievalMatch] = []
        for chunk_id, score in ranked:
            if chunk_id not in chunks:
                continue
            chunk, document_type = chunks[chunk_id]
            matches.append(
                self._match(
                    chunk,
                    document_type,
                    query,
                    bm25_score=score,
                )
            )
        return matches

    def _search_short_terms(
        self,
        scope: KnowledgeScope,
        knowledge_base_id: UUID,
        query: NormalizedQuery,
        *,
        limit: int,
        document_ids: list[UUID] | None,
        document_types: list[str] | None,
        filenames: list[str] | None,
        versions: list[int] | None,
    ) -> list[RetrievalMatch]:
        rows = self.store.search_short_terms(
            scope,
            knowledge_base_id,
            query.short_terms,
            limit=limit,
            document_ids=document_ids,
            document_types=document_types,
            filenames=filenames,
            versions=versions,
        )
        return [
            self._match(
                chunk,
                document_type,
                query,
                bm25_score=None,
            )
            for chunk, document_type, _hit_count in rows
        ]

    @staticmethod
    def _match(
        chunk: KnowledgeChunk,
        document_type: str,
        query: NormalizedQuery,
        *,
        bm25_score: float | None,
    ) -> RetrievalMatch:
        searchable_metadata = " ".join(
            [
                chunk.search_text,
                chunk.filename.casefold(),
                document_type.casefold(),
                *(value.casefold() for value in chunk.section_path),
            ]
        )
        matched_terms = [
            term
            for term in query.terms
            if term.casefold() in searchable_metadata
        ]
        return RetrievalMatch(
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
            bm25_score=bm25_score,
            matched_terms=matched_terms,
            rank=0,
            mapping_version=query.mapping_version,
        )

    @staticmethod
    def _deduplicate(
        candidates: list[RetrievalMatch],
    ) -> list[RetrievalMatch]:
        result: list[RetrievalMatch] = []
        seen: set[UUID] = set()
        for match in candidates:
            if match.chunk_id not in seen:
                seen.add(match.chunk_id)
                result.append(match)
        return result

    @staticmethod
    def _can_apply_comparison_coverage(
        query: NormalizedQuery,
        top_k: int,
        document_types: list[str] | None,
    ) -> bool:
        if not query.comparison_intent or top_k < 2:
            return False
        if document_types is None:
            return True
        return {"resume", "job_description"}.issubset(document_types)

    @staticmethod
    def _ensure_comparison_coverage(
        candidates: list[RetrievalMatch],
    ) -> list[RetrievalMatch]:
        resume = next(
            (
                match
                for match in candidates
                if match.document_type == "resume"
            ),
            None,
        )
        job = next(
            (
                match
                for match in candidates
                if match.document_type == "job_description"
            ),
            None,
        )
        if resume is None or job is None:
            return candidates
        selected = {resume.chunk_id, job.chunk_id}
        return [
            resume,
            job,
            *[
                match
                for match in candidates
                if match.chunk_id not in selected
            ],
        ]
