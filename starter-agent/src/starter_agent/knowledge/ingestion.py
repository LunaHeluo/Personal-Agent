from __future__ import annotations

from starter_agent.knowledge.chunker import KnowledgeChunker
from starter_agent.knowledge.errors import KnowledgeError
from starter_agent.knowledge.models import KnowledgeScope, UploadBundle
from starter_agent.knowledge.parser import MarkdownParser
from starter_agent.knowledge.store import SQLiteKnowledgeStore


class KnowledgeIngestionPipeline:
    def __init__(
        self,
        store: SQLiteKnowledgeStore,
        *,
        target_chars: int,
        overlap_chars: int,
        max_chunks: int = 5_000,
    ) -> None:
        self.store = store
        self.parser = MarkdownParser()
        self.chunker = KnowledgeChunker(
            target_chars=target_chars,
            max_chars=max(1800, target_chars),
            overlap_chars=overlap_chars,
        )
        self.max_chunks = max_chunks

    def run(self, scope: KnowledgeScope, upload: UploadBundle) -> None:
        stage = "parse"
        try:
            self.store.mark_job_running(scope, upload.job.id, stage=stage)
            parsed = self.parser.parse(upload.version.source_text)
            stage = "chunk"
            self.store.mark_job_running(scope, upload.job.id, stage=stage)
            chunks = self.chunker.chunk(
                parsed=parsed,
                scope=scope,
                knowledge_base_id=upload.document.knowledge_base_id,
                document_id=upload.document.id,
                version_id=upload.version.id,
                version=upload.version.version,
                filename=upload.document.filename,
            )
            if len(chunks) > self.max_chunks:
                raise KnowledgeError("knowledge_chunk_capacity_exceeded")
            active_elsewhere = self.store.count_active_chunks(
                scope,
                upload.document.knowledge_base_id,
                exclude_document_id=upload.document.id,
            )
            if active_elsewhere + len(chunks) > self.max_chunks:
                raise KnowledgeError("knowledge_chunk_capacity_exceeded")
            self.store.complete_chunking(scope, upload, chunks)
        except KnowledgeError as error:
            self.store.fail_ingestion(
                scope, upload, error_code=error.code, stage=stage
            )
        except Exception:
            self.store.fail_ingestion(
                scope,
                upload,
                error_code="document_ingestion_failed",
                stage=stage,
            )
