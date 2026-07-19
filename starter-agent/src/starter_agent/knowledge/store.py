from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, create_engine, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from starter_agent.knowledge.errors import KnowledgeError
from starter_agent.knowledge.models import (
    DocumentVersion,
    IngestionJob,
    KnowledgeBase,
    KnowledgeDocument,
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
        KnowledgeBaseSql.metadata.create_all(self.engine)

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
    def _document(row: KnowledgeDocumentRow) -> KnowledgeDocument:
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
            db.add_all([document, version, job])
            db.commit()
            db.refresh(document)
            db.refresh(version)
            db.refresh(job)
            return UploadBundle(
                document=self._document(document),
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
        return [self._document(row) for row in rows]

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
        return self._document(row) if row else None

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
