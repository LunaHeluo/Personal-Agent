from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, create_engine, event, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from starter_agent.knowledge.errors import KnowledgeError
from starter_agent.knowledge.models import (
    DocumentVersion,
    IngestionJob,
    KnowledgeBase,
    KnowledgeDocument,
    KnowledgeChunk,
    KnowledgeScope,
    UploadBundle,
)


class KnowledgeBaseSql(DeclarativeBase):
    pass


class KnowledgeBaseRow(KnowledgeBaseSql):
    __tablename__ = "knowledge_bases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(120), index=True)
    project_id: Mapped[str] = mapped_column(String(120), index=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class KnowledgeDocumentRow(KnowledgeBaseSql):
    __tablename__ = "knowledge_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id"), index=True
    )
    user_id: Mapped[str] = mapped_column(String(120), index=True)
    project_id: Mapped[str] = mapped_column(String(120), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    document_type: Mapped[str] = mapped_column(String(40))
    active_version_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(24), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DocumentVersionRow(KnowledgeBaseSql):
    __tablename__ = "knowledge_document_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_documents.id"), index=True
    )
    knowledge_base_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str] = mapped_column(String(120), index=True)
    project_id: Mapped[str] = mapped_column(String(120), index=True)
    version: Mapped[int] = mapped_column(Integer)
    content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    source_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), index=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    indexed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)


class IngestionJobRow(KnowledgeBaseSql):
    __tablename__ = "knowledge_ingestion_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_documents.id"), index=True
    )
    version_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_document_versions.id"), index=True
    )
    knowledge_base_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str] = mapped_column(String(120), index=True)
    project_id: Mapped[str] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    stage: Mapped[str] = mapped_column(String(24))
    progress_current: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    retryable: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class KnowledgeChunkRow(KnowledgeBaseSql):
    __tablename__ = "knowledge_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_documents.id"), index=True
    )
    version_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_document_versions.id"), index=True
    )
    knowledge_base_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str] = mapped_column(String(120), index=True)
    project_id: Mapped[str] = mapped_column(String(120), index=True)
    version: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(255))
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section_path: Mapped[str] = mapped_column(Text)
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    search_text: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class InvalidatedCitationRow(KnowledgeBaseSql):
    __tablename__ = "invalidated_citations"

    chunk_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    knowledge_base_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str] = mapped_column(String(120), index=True)
    project_id: Mapped[str] = mapped_column(String(120), index=True)
    invalidated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SQLiteKnowledgeStore:
    def __init__(self, database_url: str, project_root: Path):
        if database_url.startswith("sqlite:///"):
            relative = database_url.removeprefix("sqlite:///")
            database_path = Path(relative)
            if not database_path.is_absolute():
                database_path = project_root / database_path
            database_path.parent.mkdir(parents=True, exist_ok=True)
            database_url = f"sqlite:///{database_path}"
        self.engine = create_engine(database_url)
        if self.engine.dialect.name == "sqlite":
            @event.listens_for(self.engine, "connect")
            def _configure_sqlite(dbapi_connection, _record) -> None:
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA secure_delete=ON")
                cursor.close()
        KnowledgeBaseSql.metadata.create_all(self.engine)
        from starter_agent.knowledge.index import SQLiteFtsIndex

        self.index = SQLiteFtsIndex(self.engine)
        self.index.ensure_available()

    @staticmethod
    def _scope_filters(scope: KnowledgeScope) -> tuple[object, object]:
        return (
            KnowledgeDocumentRow.user_id == scope.user_id,
            KnowledgeDocumentRow.project_id == scope.project_id,
        )

    @staticmethod
    def _base(row: KnowledgeBaseRow) -> KnowledgeBase:
        return KnowledgeBase(
            id=UUID(row.id),
            user_id=row.user_id,
            project_id=row.project_id,
            name=row.name,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _document(
        row: KnowledgeDocumentRow,
        version: DocumentVersionRow | None = None,
    ) -> KnowledgeDocument:
        return KnowledgeDocument(
            id=UUID(row.id),
            knowledge_base_id=UUID(row.knowledge_base_id),
            user_id=row.user_id,
            project_id=row.project_id,
            filename=row.filename,
            document_type=row.document_type,
            active_version_id=(
                UUID(row.active_version_id) if row.active_version_id else None
            ),
            status=row.status,  # type: ignore[arg-type]
            version=version.version if version else None,
            content_sha256=version.content_sha256 if version else None,
            chunk_count=version.chunk_count if version else 0,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _version(row: DocumentVersionRow) -> DocumentVersion:
        return DocumentVersion(
            id=UUID(row.id),
            document_id=UUID(row.document_id),
            knowledge_base_id=UUID(row.knowledge_base_id),
            version=row.version,
            content_sha256=row.content_sha256,
            source_text=row.source_text,
            status=row.status,  # type: ignore[arg-type]
            chunk_count=row.chunk_count,
            created_at=row.created_at,
            indexed_at=row.indexed_at,
            error_code=row.error_code,
        )

    @staticmethod
    def _job(row: IngestionJobRow) -> IngestionJob:
        return IngestionJob(
            id=UUID(row.id),
            document_id=UUID(row.document_id),
            version_id=UUID(row.version_id),
            knowledge_base_id=UUID(row.knowledge_base_id),
            user_id=row.user_id,
            project_id=row.project_id,
            status=row.status,  # type: ignore[arg-type]
            stage=row.stage,  # type: ignore[arg-type]
            progress_current=row.progress_current,
            progress_total=row.progress_total,
            error_code=row.error_code,
            retryable=bool(row.retryable),
            created_at=row.created_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
        )

    @staticmethod
    def _chunk(row: KnowledgeChunkRow) -> KnowledgeChunk:
        return KnowledgeChunk(
            id=UUID(row.id),
            document_id=UUID(row.document_id),
            version_id=UUID(row.version_id),
            knowledge_base_id=UUID(row.knowledge_base_id),
            user_id=row.user_id,
            project_id=row.project_id,
            version=row.version,
            filename=row.filename,
            page=row.page,
            section_path=json.loads(row.section_path),
            start_line=row.start_line,
            end_line=row.end_line,
            ordinal=row.ordinal,
            text=row.text,
            search_text=row.search_text,
            content_sha256=row.content_sha256,
            created_at=row.created_at,
        )

    def ensure_knowledge_base(
        self,
        scope: KnowledgeScope,
        *,
        knowledge_base_id: UUID,
        name: str,
    ) -> KnowledgeBase:
        now = datetime.now(UTC)
        with Session(self.engine) as db:
            row = db.get(KnowledgeBaseRow, str(knowledge_base_id))
            if row is None:
                row = KnowledgeBaseRow(
                    id=str(knowledge_base_id),
                    user_id=scope.user_id,
                    project_id=scope.project_id,
                    name=name,
                    created_at=now,
                    updated_at=now,
                )
                db.add(row)
                db.commit()
                db.refresh(row)
            elif row.user_id != scope.user_id or row.project_id != scope.project_id:
                raise KnowledgeError("knowledge_base_not_found")
            return self._base(row)

    def list_knowledge_bases(self, scope: KnowledgeScope) -> list[KnowledgeBase]:
        with Session(self.engine) as db:
            rows = list(
                db.scalars(
                    select(KnowledgeBaseRow)
                    .where(
                        KnowledgeBaseRow.user_id == scope.user_id,
                        KnowledgeBaseRow.project_id == scope.project_id,
                    )
                    .order_by(KnowledgeBaseRow.created_at)
                )
            )
        return [self._base(row) for row in rows]

    def count_documents(
        self, scope: KnowledgeScope, knowledge_base_id: UUID
    ) -> int:
        with Session(self.engine) as db:
            return int(
                db.scalar(
                    select(func.count())
                    .select_from(KnowledgeDocumentRow)
                    .where(
                        *self._scope_filters(scope),
                        KnowledgeDocumentRow.knowledge_base_id
                        == str(knowledge_base_id),
                    )
                )
                or 0
            )

    def create_upload(
        self,
        scope: KnowledgeScope,
        *,
        knowledge_base_id: UUID,
        filename: str,
        document_type: str,
        source_text: str,
        content_sha256: str,
    ) -> UploadBundle:
        now = datetime.now(UTC)
        document_id = uuid4()
        version_id = uuid4()
        job_id = uuid4()
        with Session(self.engine) as db:
            base = db.get(KnowledgeBaseRow, str(knowledge_base_id))
            if (
                base is None
                or base.user_id != scope.user_id
                or base.project_id != scope.project_id
            ):
                raise KnowledgeError("knowledge_base_not_found")
            duplicate = db.scalar(
                select(DocumentVersionRow.id)
                .where(
                    DocumentVersionRow.user_id == scope.user_id,
                    DocumentVersionRow.project_id == scope.project_id,
                    DocumentVersionRow.knowledge_base_id
                    == str(knowledge_base_id),
                    DocumentVersionRow.content_sha256 == content_sha256,
                )
                .limit(1)
            )
            if duplicate:
                raise KnowledgeError("duplicate_document_content")
            document = KnowledgeDocumentRow(
                id=str(document_id),
                knowledge_base_id=str(knowledge_base_id),
                user_id=scope.user_id,
                project_id=scope.project_id,
                filename=filename,
                document_type=document_type,
                active_version_id=None,
                status="queued",
                created_at=now,
                updated_at=now,
            )
            version = DocumentVersionRow(
                id=str(version_id),
                document_id=str(document_id),
                knowledge_base_id=str(knowledge_base_id),
                user_id=scope.user_id,
                project_id=scope.project_id,
                version=1,
                content_sha256=content_sha256,
                source_text=source_text,
                status="queued",
                chunk_count=0,
                created_at=now,
            )
            job = IngestionJobRow(
                id=str(job_id),
                document_id=str(document_id),
                version_id=str(version_id),
                knowledge_base_id=str(knowledge_base_id),
                user_id=scope.user_id,
                project_id=scope.project_id,
                status="queued",
                stage="upload",
                progress_current=0,
                progress_total=0,
                retryable=0,
                created_at=now,
            )
            db.add(document)
            db.flush()
            db.add(version)
            db.flush()
            db.add(job)
            db.commit()
            db.refresh(document)
            db.refresh(version)
            db.refresh(job)
            return UploadBundle(
                document=self._document(document),
                version=self._version(version),
                job=self._job(job),
            )

    def create_update(
        self,
        scope: KnowledgeScope,
        *,
        knowledge_base_id: UUID,
        document_id: UUID,
        expected_content_sha256: str,
        source_text: str,
        content_sha256: str,
    ) -> UploadBundle:
        now = datetime.now(UTC)
        version_id = uuid4()
        job_id = uuid4()
        with Session(self.engine) as db, db.begin():
            document = db.scalar(
                select(KnowledgeDocumentRow).where(
                    *self._scope_filters(scope),
                    KnowledgeDocumentRow.knowledge_base_id == str(knowledge_base_id),
                    KnowledgeDocumentRow.id == str(document_id),
                )
            )
            if document is None or not document.active_version_id:
                raise KnowledgeError("document_not_found")
            active = db.get(DocumentVersionRow, document.active_version_id)
            if active is None or active.content_sha256 != expected_content_sha256:
                raise KnowledgeError("document_version_conflict")
            if content_sha256 == active.content_sha256:
                raise KnowledgeError("duplicate_document_content")
            version = DocumentVersionRow(
                id=str(version_id),
                document_id=document.id,
                knowledge_base_id=document.knowledge_base_id,
                user_id=scope.user_id,
                project_id=scope.project_id,
                version=active.version + 1,
                content_sha256=content_sha256,
                source_text=source_text,
                status="queued",
                chunk_count=0,
                created_at=now,
            )
            job = IngestionJobRow(
                id=str(job_id),
                document_id=document.id,
                version_id=str(version_id),
                knowledge_base_id=document.knowledge_base_id,
                user_id=scope.user_id,
                project_id=scope.project_id,
                status="queued",
                stage="upload",
                retryable=0,
                created_at=now,
            )
            db.add(version)
            db.flush()
            db.add(job)
            db.flush()
            return UploadBundle(
                document=self._document(document, active),
                version=self._version(version),
                job=self._job(job),
            )

    def list_documents(
        self, scope: KnowledgeScope, knowledge_base_id: UUID
    ) -> list[KnowledgeDocument]:
        with Session(self.engine) as db:
            rows = list(
                db.scalars(
                    select(KnowledgeDocumentRow)
                    .where(
                        *self._scope_filters(scope),
                        KnowledgeDocumentRow.knowledge_base_id
                        == str(knowledge_base_id),
                    )
                    .order_by(KnowledgeDocumentRow.created_at)
                )
            )
            versions = {
                row.active_version_id: db.get(
                    DocumentVersionRow, row.active_version_id
                )
                for row in rows
                if row.active_version_id
            }
            return [
                self._document(
                    row,
                    versions.get(row.active_version_id)
                    if row.active_version_id
                    else None,
                )
                for row in rows
            ]

    def get_document(
        self,
        scope: KnowledgeScope,
        knowledge_base_id: UUID,
        document_id: UUID,
    ) -> KnowledgeDocument | None:
        with Session(self.engine) as db:
            row = db.scalar(
                select(KnowledgeDocumentRow).where(
                    *self._scope_filters(scope),
                    KnowledgeDocumentRow.knowledge_base_id
                    == str(knowledge_base_id),
                    KnowledgeDocumentRow.id == str(document_id),
                )
            )
            version = (
                db.get(DocumentVersionRow, row.active_version_id)
                if row and row.active_version_id
                else None
            )
        return self._document(row, version) if row else None

    def get_job(
        self,
        scope: KnowledgeScope,
        knowledge_base_id: UUID,
        job_id: UUID,
    ) -> IngestionJob | None:
        with Session(self.engine) as db:
            row = db.scalar(
                select(IngestionJobRow).where(
                    IngestionJobRow.user_id == scope.user_id,
                    IngestionJobRow.project_id == scope.project_id,
                    IngestionJobRow.knowledge_base_id == str(knowledge_base_id),
                    IngestionJobRow.id == str(job_id),
                )
            )
        return self._job(row) if row else None

    def complete_chunking(
        self,
        scope: KnowledgeScope,
        upload: UploadBundle,
        chunks: list[KnowledgeChunk],
    ) -> None:
        now = datetime.now(UTC)
        with Session(self.engine) as db, db.begin():
            document = db.scalar(
                select(KnowledgeDocumentRow).where(
                    *self._scope_filters(scope),
                    KnowledgeDocumentRow.id == str(upload.document.id),
                    KnowledgeDocumentRow.knowledge_base_id
                    == str(upload.document.knowledge_base_id),
                )
            )
            version = db.get(DocumentVersionRow, str(upload.version.id))
            job = db.get(IngestionJobRow, str(upload.job.id))
            if document is None or version is None or job is None:
                raise KnowledgeError("document_not_found")
            old_version_id = document.active_version_id
            if old_version_id and old_version_id != version.id:
                old_chunks = list(
                    db.scalars(
                        select(KnowledgeChunkRow).where(
                            KnowledgeChunkRow.version_id == old_version_id
                        )
                    )
                )
                for old_chunk in old_chunks:
                    db.merge(
                        InvalidatedCitationRow(
                            chunk_hash=sha256(old_chunk.id.encode()).hexdigest(),
                            knowledge_base_id=document.knowledge_base_id,
                            user_id=scope.user_id,
                            project_id=scope.project_id,
                            invalidated_at=now,
                        )
                    )
                db.query(KnowledgeChunkRow).filter(
                    KnowledgeChunkRow.version_id == old_version_id
                ).delete()
            db.query(KnowledgeChunkRow).filter(
                KnowledgeChunkRow.version_id == str(upload.version.id)
            ).delete()
            db.add_all(
                [
                    KnowledgeChunkRow(
                        id=str(item.id),
                        document_id=str(item.document_id),
                        version_id=str(item.version_id),
                        knowledge_base_id=str(item.knowledge_base_id),
                        user_id=item.user_id,
                        project_id=item.project_id,
                        version=item.version,
                        filename=item.filename,
                        page=item.page,
                        section_path=json.dumps(
                            item.section_path, ensure_ascii=False
                        ),
                        start_line=item.start_line,
                        end_line=item.end_line,
                        ordinal=item.ordinal,
                        text=item.text,
                        search_text=item.search_text,
                        content_sha256=item.content_sha256,
                        created_at=item.created_at,
                    )
                    for item in chunks
                ]
            )
            version.status = "indexed"
            version.chunk_count = len(chunks)
            version.indexed_at = now
            document.active_version_id = version.id
            document.status = "indexed"
            document.updated_at = now
            job.status = "succeeded"
            job.stage = "metadata"
            job.progress_current = len(chunks)
            job.progress_total = len(chunks)
            job.started_at = job.started_at or now
            job.finished_at = now
            db.flush()
            self.index.rebuild(db.connection())
            if old_version_id and old_version_id != version.id:
                db.query(IngestionJobRow).filter(
                    IngestionJobRow.version_id == old_version_id
                ).delete()
                db.query(DocumentVersionRow).filter(
                    DocumentVersionRow.id == old_version_id
                ).delete()

    def fail_ingestion(
        self,
        scope: KnowledgeScope,
        upload: UploadBundle,
        *,
        error_code: str,
        stage: str,
    ) -> None:
        now = datetime.now(UTC)
        with Session(self.engine) as db, db.begin():
            document = db.scalar(
                select(KnowledgeDocumentRow).where(
                    *self._scope_filters(scope),
                    KnowledgeDocumentRow.id == str(upload.document.id),
                )
            )
            version = db.get(DocumentVersionRow, str(upload.version.id))
            job = db.get(IngestionJobRow, str(upload.job.id))
            if document and version and job:
                document.status = "failed"
                version.status = "failed"
                version.error_code = error_code
                job.status = "failed"
                job.stage = stage
                job.error_code = error_code
                job.finished_at = now

    def list_chunks(
        self,
        scope: KnowledgeScope,
        knowledge_base_id: UUID,
        document_id: UUID,
        *,
        after_ordinal: int,
        limit: int,
    ) -> list[KnowledgeChunk]:
        with Session(self.engine) as db:
            rows = list(
                db.scalars(
                    select(KnowledgeChunkRow)
                    .join(
                        KnowledgeDocumentRow,
                        KnowledgeDocumentRow.id == KnowledgeChunkRow.document_id,
                    )
                    .where(
                        *self._scope_filters(scope),
                        KnowledgeChunkRow.knowledge_base_id
                        == str(knowledge_base_id),
                        KnowledgeChunkRow.document_id == str(document_id),
                        KnowledgeDocumentRow.active_version_id
                        == KnowledgeChunkRow.version_id,
                        KnowledgeChunkRow.ordinal > after_ordinal,
                    )
                    .order_by(KnowledgeChunkRow.ordinal)
                    .limit(limit)
                )
            )
        return [self._chunk(row) for row in rows]

    def get_chunks_by_ids(
        self,
        scope: KnowledgeScope,
        knowledge_base_id: UUID,
        chunk_ids: list[UUID],
    ) -> dict[UUID, tuple[KnowledgeChunk, str]]:
        if not chunk_ids:
            return {}
        with Session(self.engine) as db:
            rows = db.execute(
                select(KnowledgeChunkRow, KnowledgeDocumentRow.document_type)
                .join(
                    KnowledgeDocumentRow,
                    KnowledgeDocumentRow.id == KnowledgeChunkRow.document_id,
                )
                .where(
                    *self._scope_filters(scope),
                    KnowledgeChunkRow.knowledge_base_id == str(knowledge_base_id),
                    KnowledgeDocumentRow.active_version_id
                    == KnowledgeChunkRow.version_id,
                    KnowledgeChunkRow.id.in_([str(value) for value in chunk_ids]),
                )
            ).all()
        return {
            UUID(row[0].id): (self._chunk(row[0]), row[1]) for row in rows
        }

    def search_short_terms(
        self,
        scope: KnowledgeScope,
        knowledge_base_id: UUID,
        terms: list[str],
        *,
        limit: int,
        document_ids: list[UUID] | None = None,
        document_types: list[str] | None = None,
        filenames: list[str] | None = None,
        versions: list[int] | None = None,
    ) -> list[tuple[KnowledgeChunk, str]]:
        if not terms:
            return []
        query = (
            select(KnowledgeChunkRow, KnowledgeDocumentRow.document_type)
            .join(
                KnowledgeDocumentRow,
                KnowledgeDocumentRow.id == KnowledgeChunkRow.document_id,
            )
            .where(
                *self._scope_filters(scope),
                KnowledgeChunkRow.knowledge_base_id == str(knowledge_base_id),
                KnowledgeDocumentRow.active_version_id
                == KnowledgeChunkRow.version_id,
                *[
                    func.instr(KnowledgeChunkRow.search_text, term) > 0
                    for term in terms
                ],
            )
        )
        if document_ids:
            query = query.where(
                KnowledgeChunkRow.document_id.in_(
                    [str(value) for value in document_ids]
                )
            )
        if document_types:
            query = query.where(
                KnowledgeDocumentRow.document_type.in_(document_types)
            )
        if filenames:
            query = query.where(KnowledgeChunkRow.filename.in_(filenames))
        if versions:
            query = query.where(KnowledgeChunkRow.version.in_(versions))
        query = query.order_by(
            KnowledgeChunkRow.document_id, KnowledgeChunkRow.ordinal
        ).limit(min(limit, 100))
        with Session(self.engine) as db:
            rows = db.execute(query).all()
        return [(self._chunk(row[0]), row[1]) for row in rows]

    def citation_state(
        self,
        scope: KnowledgeScope,
        knowledge_base_id: UUID,
        chunk_id: UUID,
    ) -> KnowledgeChunk | None:
        chunks = self.get_chunks_by_ids(scope, knowledge_base_id, [chunk_id])
        if chunk_id in chunks:
            return chunks[chunk_id][0]
        digest = sha256(str(chunk_id).encode()).hexdigest()
        with Session(self.engine) as db:
            invalidated = db.scalar(
                select(InvalidatedCitationRow.chunk_hash).where(
                    InvalidatedCitationRow.chunk_hash == digest,
                    InvalidatedCitationRow.user_id == scope.user_id,
                    InvalidatedCitationRow.project_id == scope.project_id,
                    InvalidatedCitationRow.knowledge_base_id == str(knowledge_base_id),
                )
            )
        if invalidated:
            raise KnowledgeError("citation_gone")
        return None

    def delete_document(
        self,
        scope: KnowledgeScope,
        knowledge_base_id: UUID,
        document_id: UUID,
    ) -> bool:
        with Session(self.engine) as db, db.begin():
            document = db.scalar(
                select(KnowledgeDocumentRow).where(
                    *self._scope_filters(scope),
                    KnowledgeDocumentRow.knowledge_base_id == str(knowledge_base_id),
                    KnowledgeDocumentRow.id == str(document_id),
                )
            )
            if document is None:
                return False
            version_ids = list(
                db.scalars(
                    select(DocumentVersionRow.id).where(
                        DocumentVersionRow.document_id == document.id
                    )
                )
            )
            db.query(KnowledgeChunkRow).filter(
                KnowledgeChunkRow.document_id == document.id
            ).delete()
            db.query(IngestionJobRow).filter(
                IngestionJobRow.document_id == document.id
            ).delete()
            db.query(DocumentVersionRow).filter(
                DocumentVersionRow.id.in_(version_ids)
            ).delete(synchronize_session=False)
            db.query(InvalidatedCitationRow).filter(
                InvalidatedCitationRow.user_id == scope.user_id,
                InvalidatedCitationRow.project_id == scope.project_id,
                InvalidatedCitationRow.knowledge_base_id == str(knowledge_base_id),
            ).delete()
            db.delete(document)
            db.flush()
            self.index.rebuild(db.connection())
        return True
