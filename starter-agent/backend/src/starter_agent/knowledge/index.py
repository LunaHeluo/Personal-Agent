from __future__ import annotations

from uuid import UUID

from sqlalchemy import Engine, text
from sqlalchemy.exc import DatabaseError

from starter_agent.knowledge.errors import KnowledgeError
from starter_agent.knowledge.models import KnowledgeScope


class SQLiteFtsIndex:
    def __init__(self, engine: Engine):
        self.engine = engine

    def ensure_available(self) -> None:
        try:
            with self.engine.begin() as connection:
                connection.exec_driver_sql(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunks_fts "
                    "USING fts5(search_text, section_path, "
                    "content='knowledge_chunks', content_rowid='rowid', "
                    "tokenize='trigram')"
                )
                connection.exec_driver_sql(
                    "INSERT INTO knowledge_chunks_fts(knowledge_chunks_fts) "
                    "VALUES('rebuild')"
                )
        except DatabaseError as exc:
            message = str(exc).lower()
            code = (
                "fts5_trigram_unavailable"
                if "tokenizer" in message or "trigram" in message
                else "fts5_unavailable"
            )
            raise KnowledgeError(code) from exc

    def rebuild(self, connection) -> None:
        connection.exec_driver_sql(
            "INSERT INTO knowledge_chunks_fts(knowledge_chunks_fts) VALUES('rebuild')"
        )

    def search(
        self,
        scope: KnowledgeScope,
        knowledge_base_id: UUID,
        match_expression: str,
        *,
        limit: int,
        document_ids: list[UUID] | None = None,
        document_types: list[str] | None = None,
        filenames: list[str] | None = None,
        versions: list[int] | None = None,
    ) -> list[tuple[UUID, float]]:
        conditions = [
            "d.user_id = :user_id",
            "d.project_id = :project_id",
            "c.knowledge_base_id = :base_id",
            "d.active_version_id = c.version_id",
            "knowledge_chunks_fts MATCH :match",
        ]
        params: dict[str, object] = {
            "user_id": scope.user_id,
            "project_id": scope.project_id,
            "base_id": str(knowledge_base_id),
            "match": match_expression,
            "limit": limit,
        }
        for name, values, column in (
            ("document_id", document_ids, "c.document_id"),
            ("document_type", document_types, "d.document_type"),
            ("filename", filenames, "c.filename"),
            ("version", versions, "c.version"),
        ):
            if values:
                keys = []
                for index, value in enumerate(values):
                    key = f"{name}_{index}"
                    keys.append(f":{key}")
                    params[key] = str(value) if isinstance(value, UUID) else value
                conditions.append(f"{column} IN ({','.join(keys)})")
        sql = text(
            "SELECT c.id, bm25(knowledge_chunks_fts) AS score "
            "FROM knowledge_chunks_fts "
            "JOIN knowledge_chunks c ON c.rowid = knowledge_chunks_fts.rowid "
            "JOIN knowledge_documents d ON d.id = c.document_id "
            f"WHERE {' AND '.join(conditions)} "
            "ORDER BY score ASC, c.document_id ASC, c.ordinal ASC LIMIT :limit"
        )
        with self.engine.connect() as connection:
            rows = connection.execute(sql, params).all()
        return [(UUID(row[0]), float(row[1])) for row in rows]
