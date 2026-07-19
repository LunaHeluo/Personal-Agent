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
    ) -> None:
        self.store = store
        self.parser = MarkdownParser()
        self.chunker = KnowledgeChunker(
            target_chars=target_chars,
            max_chars=max(1800, target_chars),
            overlap_chars=overlap_chars,
        )

    def run(self, scope: KnowledgeScope, upload: UploadBundle) -> None:
        stage = "parse"
        try:
            parsed = self.parser.parse(upload.version.source_text)
            stage = "chunk"
            chunks = self.chunker.chunk(
                parsed=parsed,
                scope=scope,
                knowledge_base_id=upload.document.knowledge_base_id,
                document_id=upload.document.id,
                version_id=upload.version.id,
                version=upload.version.version,
                filename=upload.document.filename,
            )
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
