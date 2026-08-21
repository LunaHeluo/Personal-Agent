from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Callable
from typing import Any, Iterable

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    delete,
    event,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import StaticPool

from starter_agent.capabilities.models import (
    AuditEvent,
    BuiltinToolOverride,
    Confirmation,
    ConfirmationDecision,
    ExecutionPermit,
    PolicyRule,
    Prompt,
    Resource,
    Server,
    SkillRecord,
    Snapshot,
    Tool,
)


class CapabilityStoreError(RuntimeError):
    pass


class RecordAlreadyExistsError(CapabilityStoreError):
    pass


class RecordNotFoundError(CapabilityStoreError):
    pass


class RevisionConflictError(CapabilityStoreError):
    pass


class ExecutionPermitError(CapabilityStoreError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ConfirmationExecutionError(CapabilityStoreError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CapabilityBase(DeclarativeBase):
    pass


class McpServerRow(CapabilityBase):
    __tablename__ = "mcp_servers"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    config_hash: Mapped[str] = mapped_column(String(64), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, index=True)
    connection_state: Mapped[str] = mapped_column(String(40), index=True)
    health_state: Mapped[str] = mapped_column(String(40), index=True)
    operation_state: Mapped[str] = mapped_column(String(40), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    payload_json: Mapped[str] = mapped_column(Text)


class CapabilitySnapshotRow(CapabilityBase):
    __tablename__ = "mcp_capability_snapshots"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    server_id: Mapped[str] = mapped_column(
        String(160),
        ForeignKey("mcp_servers.id"),
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer)
    schema_hash: Mapped[str] = mapped_column(String(64), index=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    stale: Mapped[bool] = mapped_column(Boolean, index=True)
    active: Mapped[bool] = mapped_column(Boolean, index=True)
    payload_json: Mapped[str] = mapped_column(Text)


ACTIVE_SNAPSHOT_INDEX = Index(
    "uq_mcp_capability_snapshots_server_active",
    CapabilitySnapshotRow.server_id,
    unique=True,
    sqlite_where=CapabilitySnapshotRow.active.is_(True),
)


class McpToolRow(CapabilityBase):
    __tablename__ = "mcp_tools"

    snapshot_id: Mapped[str] = mapped_column(
        String(160),
        ForeignKey("mcp_capability_snapshots.id"),
        primary_key=True,
    )
    upstream_name: Mapped[str] = mapped_column(String(200), primary_key=True)
    server_id: Mapped[str] = mapped_column(String(160), index=True)
    model_alias: Mapped[str] = mapped_column(String(200), index=True)
    schema_hash: Mapped[str] = mapped_column(String(64), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, index=True)
    review_state: Mapped[str] = mapped_column(String(40), index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    payload_json: Mapped[str] = mapped_column(Text)


class BuiltinToolOverrideRow(CapabilityBase):
    __tablename__ = "builtin_tool_overrides"

    tool_name: Mapped[str] = mapped_column(String(160), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, index=True)
    revision: Mapped[int] = mapped_column(Integer)
    payload_json: Mapped[str] = mapped_column(Text)


class McpResourceRow(CapabilityBase):
    __tablename__ = "mcp_resources"

    snapshot_id: Mapped[str] = mapped_column(
        String(160),
        ForeignKey("mcp_capability_snapshots.id"),
        primary_key=True,
    )
    name: Mapped[str] = mapped_column(String(200), primary_key=True)
    server_id: Mapped[str] = mapped_column(String(160), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, index=True)
    payload_json: Mapped[str] = mapped_column(Text)


class McpPromptRow(CapabilityBase):
    __tablename__ = "mcp_prompts"

    snapshot_id: Mapped[str] = mapped_column(
        String(160),
        ForeignKey("mcp_capability_snapshots.id"),
        primary_key=True,
    )
    name: Mapped[str] = mapped_column(String(200), primary_key=True)
    server_id: Mapped[str] = mapped_column(String(160), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, index=True)
    payload_json: Mapped[str] = mapped_column(Text)


class PolicyRuleRow(CapabilityBase):
    __tablename__ = "tool_policy_rules"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    server_id: Mapped[str] = mapped_column(String(160), index=True)
    tool_name: Mapped[str] = mapped_column(String(200), index=True)
    effect: Mapped[str] = mapped_column(String(40), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, index=True)
    revision: Mapped[int] = mapped_column(Integer)
    payload_json: Mapped[str] = mapped_column(Text)


class ConfirmationRow(CapabilityBase):
    __tablename__ = "tool_confirmations"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    server_id: Mapped[str] = mapped_column(String(160), index=True)
    tool_name: Mapped[str] = mapped_column(String(200), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    decision: Mapped[str | None] = mapped_column(String(40), nullable=True)
    idempotency_key_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    payload_json: Mapped[str] = mapped_column(Text)


class ExecutionPermitRow(CapabilityBase):
    __tablename__ = "execution_permits"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    confirmation_id: Mapped[str | None] = mapped_column(
        String(160),
        nullable=True,
        index=True,
    )
    request_hash: Mapped[str] = mapped_column(String(64), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    payload_json: Mapped[str] = mapped_column(Text)


class SkillRecordRow(CapabilityBase):
    __tablename__ = "skill_records"

    name: Mapped[str] = mapped_column(String(160), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, index=True)
    load_state: Mapped[str] = mapped_column(String(40), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    payload_json: Mapped[str] = mapped_column(Text)


class AuditEventRow(CapabilityBase):
    __tablename__ = "capability_audit_events"

    event_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    actor: Mapped[str] = mapped_column(String(200), index=True)
    action: Mapped[str] = mapped_column(String(200), index=True)
    target: Mapped[str] = mapped_column(String(500), index=True)
    decision: Mapped[str] = mapped_column(String(40), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload_json: Mapped[str] = mapped_column(Text)


class CapabilityStore:
    """Additive SQLite persistence for capability governance records."""

    def __init__(self, database_url: str, project_root: Path):
        self._audit_sinks: list[Callable[[AuditEvent], None]] = []
        engine_options: dict[str, Any] = {}
        if database_url == "sqlite:///:memory:":
            engine_options = {
                "poolclass": StaticPool,
                "connect_args": {"check_same_thread": False},
            }
        elif database_url.startswith("sqlite:///"):
            relative = database_url.removeprefix("sqlite:///")
            database_path = Path(relative)
            if not database_path.is_absolute():
                database_path = project_root / database_path
            database_path.parent.mkdir(parents=True, exist_ok=True)
            database_url = f"sqlite:///{database_path}"
        self.engine = create_engine(database_url, **engine_options)
        if self.engine.dialect.name == "sqlite":

            @event.listens_for(self.engine, "connect")
            def _configure_sqlite(dbapi_connection, _record) -> None:
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA secure_delete=ON")
                cursor.close()

        CapabilityBase.metadata.create_all(self.engine)
        self._migrate_sqlite_schema()
        ACTIVE_SNAPSHOT_INDEX.create(self.engine, checkfirst=True)

    def _migrate_sqlite_schema(self) -> None:
        if self.engine.dialect.name != "sqlite":
            return
        with self.engine.begin() as connection:
            columns = {
                str(row[1])
                for row in connection.exec_driver_sql(
                    "PRAGMA table_info(mcp_tools)"
                )
            }
            if "reviewed_at" not in columns:
                connection.exec_driver_sql(
                    "ALTER TABLE mcp_tools ADD COLUMN reviewed_at DATETIME"
                )

    def close(self) -> None:
        self.engine.dispose()

    def create_server(self, server: Server) -> Server:
        row = McpServerRow(
            id=server.id,
            name=server.name,
            config_hash=server.config_hash,
            enabled=server.enabled,
            connection_state=server.connection_state,
            health_state=server.health_state,
            operation_state=server.operation_state,
            revision=server.revision,
            payload_json=server.model_dump_json(),
        )
        self._add(row, f"Server already exists: {server.id}")
        return server

    def get_server(self, server_id: str) -> Server | None:
        with Session(self.engine) as db:
            row = db.get(McpServerRow, server_id)
            return None if row is None else Server.model_validate_json(row.payload_json)

    def update_server(
        self,
        server_id: str,
        *,
        expected_revision: int,
        **changes: Any,
    ) -> Server:
        allowed = set(Server.model_fields) - {"id", "revision"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Unsupported Server changes: {sorted(unknown)}")
        with Session(self.engine) as db:
            row = db.get(McpServerRow, server_id)
            if row is None:
                raise RecordNotFoundError(f"Server not found: {server_id}")
            current = Server.model_validate_json(row.payload_json)
            candidate = Server.model_validate(
                {
                    **current.model_dump(mode="python"),
                    **changes,
                    "revision": expected_revision + 1,
                }
            )
            result = db.execute(
                update(McpServerRow)
                .where(
                    McpServerRow.id == server_id,
                    McpServerRow.revision == expected_revision,
                )
                .values(
                    name=candidate.name,
                    config_hash=candidate.config_hash,
                    enabled=candidate.enabled,
                    connection_state=candidate.connection_state,
                    health_state=candidate.health_state,
                    operation_state=candidate.operation_state,
                    revision=candidate.revision,
                    payload_json=candidate.model_dump_json(),
                )
            )
            if result.rowcount != 1:
                db.rollback()
                raise RevisionConflictError(
                    f"Server revision conflict: {server_id} expected {expected_revision}"
                )
            db.commit()
            return candidate

    def create_snapshot(
        self,
        snapshot: Snapshot,
        *,
        tools: Iterable[Tool] = (),
        resources: Iterable[Resource] = (),
        prompts: Iterable[Prompt] = (),
    ) -> Snapshot:
        if snapshot.active:
            raise ValueError("Snapshots must be created inactive and activated explicitly")
        tool_items = tuple(
            tool.model_copy(update={"reviewed_at": None}) for tool in tools
        )
        resource_items = tuple(resources)
        prompt_items = tuple(prompts)
        if (
            snapshot.tool_count != len(tool_items)
            or snapshot.resource_count != len(resource_items)
            or snapshot.prompt_count != len(prompt_items)
        ):
            raise ValueError("Snapshot capability counts do not match supplied records")
        for item in (*tool_items, *resource_items, *prompt_items):
            if item.snapshot_id != snapshot.id or item.server_id != snapshot.server_id:
                raise ValueError("Snapshot capability has mismatched ownership")
        snapshot_row = CapabilitySnapshotRow(
            id=snapshot.id,
            server_id=snapshot.server_id,
            version=snapshot.version,
            schema_hash=snapshot.schema_hash,
            discovered_at=snapshot.discovered_at,
            stale=snapshot.stale,
            active=snapshot.active,
            payload_json=snapshot.model_dump_json(),
        )
        rows: list[CapabilityBase] = [snapshot_row]
        rows.extend(
            McpToolRow(
                snapshot_id=tool.snapshot_id,
                upstream_name=tool.upstream_name,
                server_id=tool.server_id,
                model_alias=tool.model_alias,
                schema_hash=tool.schema_hash,
                enabled=tool.enabled,
                review_state=tool.review_state,
                reviewed_at=tool.reviewed_at,
                payload_json=tool.model_dump_json(),
            )
            for tool in tool_items
        )
        rows.extend(
            McpResourceRow(
                snapshot_id=resource.snapshot_id,
                name=resource.name,
                server_id=resource.server_id,
                enabled=resource.enabled,
                payload_json=resource.model_dump_json(),
            )
            for resource in resource_items
        )
        rows.extend(
            McpPromptRow(
                snapshot_id=prompt.snapshot_id,
                name=prompt.name,
                server_id=prompt.server_id,
                enabled=prompt.enabled,
                payload_json=prompt.model_dump_json(),
            )
            for prompt in prompt_items
        )
        with Session(self.engine) as db:
            try:
                db.add_all(rows)
                db.commit()
            except IntegrityError as exc:
                db.rollback()
                raise RecordAlreadyExistsError(
                    f"Snapshot already exists or has invalid ownership: {snapshot.id}"
                ) from exc
        return snapshot

    def get_snapshot(self, snapshot_id: str) -> Snapshot | None:
        with Session(self.engine) as db:
            row = db.get(CapabilitySnapshotRow, snapshot_id)
            return (
                None
                if row is None
                else Snapshot.model_validate_json(row.payload_json)
            )

    def get_active_snapshot(self, server_id: str) -> Snapshot | None:
        with Session(self.engine) as db:
            row = db.scalar(
                select(CapabilitySnapshotRow).where(
                    CapabilitySnapshotRow.server_id == server_id,
                    CapabilitySnapshotRow.active.is_(True),
                )
            )
            return None if row is None else Snapshot.model_validate_json(row.payload_json)

    def get_snapshot_summary(self, server_id: str) -> Snapshot | None:
        return self.get_active_snapshot(server_id)

    def next_snapshot_version(self, server_id: str) -> int:
        with Session(self.engine) as db:
            versions = db.scalars(
                select(CapabilitySnapshotRow.version).where(
                    CapabilitySnapshotRow.server_id == server_id
                )
            ).all()
            return max(versions, default=0) + 1

    def activate_snapshot(self, server_id: str, snapshot_id: str) -> Snapshot:
        with Session(self.engine) as db:
            target = db.get(CapabilitySnapshotRow, snapshot_id)
            if target is None or target.server_id != server_id:
                raise RecordNotFoundError(
                    f"Snapshot not found for server {server_id}: {snapshot_id}"
                )
            rows = db.scalars(
                select(CapabilitySnapshotRow).where(
                    CapabilitySnapshotRow.server_id == server_id
                )
            ).all()
            for row in rows:
                current = Snapshot.model_validate_json(row.payload_json)
                if current.active:
                    inactive = Snapshot.model_validate(
                        {**current.model_dump(mode="python"), "active": False}
                    )
                    row.active = False
                    row.payload_json = inactive.model_dump_json()
            db.flush()
            target_current = Snapshot.model_validate_json(target.payload_json)
            selected = Snapshot.model_validate(
                {**target_current.model_dump(mode="python"), "active": True}
            )
            target.active = True
            target.payload_json = selected.model_dump_json()
            db.flush()
            db.commit()
            return selected

    def activate_refreshed_snapshot(
        self,
        server_id: str,
        snapshot_id: str,
    ) -> Snapshot:
        """Activate a candidate and invalidate governance for changed identities."""
        now = datetime.now(UTC)
        with Session(self.engine) as db:
            target = db.get(CapabilitySnapshotRow, snapshot_id)
            if target is None or target.server_id != server_id:
                raise RecordNotFoundError(
                    f"Snapshot not found for server {server_id}: {snapshot_id}"
                )
            current = db.scalar(
                select(CapabilitySnapshotRow).where(
                    CapabilitySnapshotRow.server_id == server_id,
                    CapabilitySnapshotRow.active.is_(True),
                )
            )
            old_tools: dict[str, Tool] = {}
            if current is not None:
                old_rows = db.scalars(
                    select(McpToolRow).where(
                        McpToolRow.snapshot_id == current.id
                    )
                ).all()
                old_tools = {
                    row.upstream_name: Tool.model_validate_json(row.payload_json)
                    for row in old_rows
                }
            new_rows = db.scalars(
                select(McpToolRow).where(McpToolRow.snapshot_id == snapshot_id)
            ).all()
            new_hashes = {row.upstream_name: row.schema_hash for row in new_rows}
            changed_names = {
                name
                for name, old_tool in old_tools.items()
                if new_hashes.get(name) != old_tool.schema_hash
            }
            for row in new_rows:
                tool = Tool.model_validate_json(row.payload_json)
                old_tool = old_tools.get(tool.upstream_name)
                if old_tool is not None and old_tool.schema_hash == tool.schema_hash:
                    tool = tool.model_copy(
                        update={
                            "enabled": old_tool.enabled,
                            "review_state": old_tool.review_state,
                            "reviewed_at": old_tool.reviewed_at,
                            "revision": old_tool.revision,
                        }
                    )
                elif old_tool is not None:
                    tool = tool.model_copy(
                        update={
                            "enabled": False,
                            "review_state": "review_required",
                            "reviewed_at": None,
                            "revision": old_tool.revision + 1,
                        }
                    )
                row.enabled = tool.enabled
                row.review_state = tool.review_state
                row.reviewed_at = tool.reviewed_at
                row.payload_json = tool.model_dump_json()

            if changed_names:
                rule_rows = db.scalars(
                    select(PolicyRuleRow).where(
                        PolicyRuleRow.server_id == server_id,
                        PolicyRuleRow.tool_name.in_(changed_names),
                        PolicyRuleRow.effect == "allowlist_auto",
                    )
                ).all()
                for row in rule_rows:
                    rule = PolicyRule.model_validate_json(row.payload_json)
                    if not rule.enabled:
                        continue
                    rule = rule.model_copy(
                        update={"enabled": False, "revision": rule.revision + 1}
                    )
                    row.enabled = False
                    row.revision = rule.revision
                    row.payload_json = rule.model_dump_json()

                confirmation_rows = db.scalars(
                    select(ConfirmationRow).where(
                        ConfirmationRow.server_id == server_id,
                        ConfirmationRow.tool_name.in_(changed_names),
                        ConfirmationRow.status.notin_(("invalidated", "expired")),
                    )
                ).all()
                invalidated_ids: list[str] = []
                for row in confirmation_rows:
                    confirmation = Confirmation.model_validate_json(row.payload_json)
                    confirmation = confirmation.model_copy(
                        update={
                            "status": "invalidated",
                            "decision": None,
                            "idempotency_key_hash": None,
                            "decided_at": now,
                            "revision": confirmation.revision + 1,
                        }
                    )
                    row.status = confirmation.status
                    row.decision = None
                    row.idempotency_key_hash = None
                    row.revision = confirmation.revision
                    row.payload_json = confirmation.model_dump_json()
                    invalidated_ids.append(confirmation.id)
                if invalidated_ids:
                    permit_rows = db.scalars(
                        select(ExecutionPermitRow).where(
                            ExecutionPermitRow.confirmation_id.in_(invalidated_ids),
                            ExecutionPermitRow.consumed_at.is_(None),
                        )
                    ).all()
                    for row in permit_rows:
                        permit = ExecutionPermit.model_validate_json(row.payload_json)
                        permit = permit.model_copy(update={"consumed_at": now})
                        row.consumed_at = now
                        row.payload_json = permit.model_dump_json()

            if current is not None:
                current_snapshot = Snapshot.model_validate_json(current.payload_json)
                inactive = current_snapshot.model_copy(update={"active": False})
                current.active = False
                current.payload_json = inactive.model_dump_json()
                db.flush()
            candidate = Snapshot.model_validate_json(target.payload_json)
            selected = candidate.model_copy(update={"active": True})
            target.active = True
            target.payload_json = selected.model_dump_json()
            db.flush()
            db.commit()
            return selected

    def mark_active_snapshot_stale(
        self,
        server_id: str,
        *,
        error: str,
    ) -> Snapshot | None:
        with Session(self.engine) as db:
            row = db.scalar(
                select(CapabilitySnapshotRow).where(
                    CapabilitySnapshotRow.server_id == server_id,
                    CapabilitySnapshotRow.active.is_(True),
                )
            )
            if row is None:
                return None
            current = Snapshot.model_validate_json(row.payload_json)
            stale = current.model_copy(update={"stale": True, "error": error})
            row.stale = True
            row.payload_json = stale.model_dump_json()
            db.commit()
            return stale

    def list_tools(self, snapshot_id: str) -> list[Tool]:
        with Session(self.engine) as db:
            rows = db.scalars(
                select(McpToolRow)
                .where(McpToolRow.snapshot_id == snapshot_id)
                .order_by(McpToolRow.upstream_name)
            ).all()
            return [Tool.model_validate_json(row.payload_json) for row in rows]

    def update_tool(
        self,
        snapshot_id: str,
        upstream_name: str,
        *,
        expected_revision: int,
        **changes: Any,
    ) -> Tool:
        allowed = {"enabled", "review_state"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Unsupported Tool changes: {sorted(unknown)}")
        with Session(self.engine) as db:
            row = db.get(McpToolRow, (snapshot_id, upstream_name))
            if row is None:
                raise RecordNotFoundError(
                    f"Tool not found: {snapshot_id}/{upstream_name}"
                )
            current = Tool.model_validate_json(row.payload_json)
            if current.revision != expected_revision:
                raise RevisionConflictError(
                    f"Tool revision conflict: {upstream_name} expected {expected_revision}"
                )
            requested_review = changes.get("review_state")
            if requested_review in {"unreviewed", "review_required"}:
                reviewed_at = None
            elif (
                requested_review in {"approved", "rejected"}
                and (
                    requested_review != current.review_state
                    or current.reviewed_at is None
                )
            ):
                reviewed_at = datetime.now(UTC)
            else:
                reviewed_at = current.reviewed_at
            candidate = Tool.model_validate(
                {
                    **current.model_dump(mode="python"),
                    **changes,
                    "reviewed_at": reviewed_at,
                    "revision": expected_revision + 1,
                }
            )
            result = db.execute(
                update(McpToolRow)
                .where(
                    McpToolRow.snapshot_id == snapshot_id,
                    McpToolRow.upstream_name == upstream_name,
                    McpToolRow.payload_json == current.model_dump_json(),
                )
                .values(
                    enabled=candidate.enabled,
                    review_state=candidate.review_state,
                    reviewed_at=candidate.reviewed_at,
                    payload_json=candidate.model_dump_json(),
                )
            )
            if result.rowcount != 1:
                db.rollback()
                raise RevisionConflictError(
                    f"Tool revision conflict: {upstream_name} expected {expected_revision}"
                )
            db.commit()
            return candidate

    def get_builtin_tool_override(
        self, tool_name: str
    ) -> BuiltinToolOverride | None:
        with Session(self.engine) as db:
            row = db.get(BuiltinToolOverrideRow, tool_name)
            return (
                None
                if row is None
                else BuiltinToolOverride.model_validate_json(row.payload_json)
            )

    def list_builtin_tool_overrides(self) -> list[BuiltinToolOverride]:
        with Session(self.engine) as db:
            rows = db.scalars(
                select(BuiltinToolOverrideRow).order_by(
                    BuiltinToolOverrideRow.tool_name
                )
            ).all()
            return [
                BuiltinToolOverride.model_validate_json(row.payload_json)
                for row in rows
            ]

    def put_builtin_tool_override(
        self,
        override: BuiltinToolOverride,
        *,
        expected_revision: int,
    ) -> BuiltinToolOverride:
        if override.revision not in {0, expected_revision}:
            raise ValueError("Builtin override revision is supplied by the store")
        candidate = override.model_copy(update={"revision": expected_revision + 1})
        with Session(self.engine) as db:
            row = db.get(BuiltinToolOverrideRow, override.tool_name)
            if row is None:
                if expected_revision != 0:
                    raise RevisionConflictError(
                        f"Builtin tool revision conflict: {override.tool_name} "
                        f"expected {expected_revision}"
                    )
                try:
                    db.add(
                        BuiltinToolOverrideRow(
                            tool_name=candidate.tool_name,
                            enabled=candidate.enabled,
                            revision=candidate.revision,
                            payload_json=candidate.model_dump_json(),
                        )
                    )
                    db.commit()
                except IntegrityError as exc:
                    db.rollback()
                    raise RevisionConflictError(
                        f"Builtin tool revision conflict: {override.tool_name} "
                        f"expected {expected_revision}"
                    ) from exc
                return candidate
            current = BuiltinToolOverride.model_validate_json(row.payload_json)
            if current.revision != expected_revision:
                raise RevisionConflictError(
                    f"Builtin tool revision conflict: {override.tool_name} "
                    f"expected {expected_revision}"
                )
            result = db.execute(
                update(BuiltinToolOverrideRow)
                .where(
                    BuiltinToolOverrideRow.tool_name == override.tool_name,
                    BuiltinToolOverrideRow.revision == expected_revision,
                )
                .values(
                    enabled=candidate.enabled,
                    revision=candidate.revision,
                    payload_json=candidate.model_dump_json(),
                )
            )
            if result.rowcount != 1:
                db.rollback()
                raise RevisionConflictError(
                    f"Builtin tool revision conflict: {override.tool_name} "
                    f"expected {expected_revision}"
                )
            db.commit()
            return candidate

    def list_resources(self, snapshot_id: str) -> list[Resource]:
        with Session(self.engine) as db:
            rows = db.scalars(
                select(McpResourceRow)
                .where(McpResourceRow.snapshot_id == snapshot_id)
                .order_by(McpResourceRow.name)
            ).all()
            return [Resource.model_validate_json(row.payload_json) for row in rows]

    def list_prompts(self, snapshot_id: str) -> list[Prompt]:
        with Session(self.engine) as db:
            rows = db.scalars(
                select(McpPromptRow)
                .where(McpPromptRow.snapshot_id == snapshot_id)
                .order_by(McpPromptRow.name)
            ).all()
            return [Prompt.model_validate_json(row.payload_json) for row in rows]

    def create_policy_rule(self, rule: PolicyRule) -> PolicyRule:
        row = PolicyRuleRow(
            id=rule.id,
            server_id=rule.server_id,
            tool_name=rule.tool_name,
            effect=rule.effect,
            enabled=rule.enabled,
            revision=rule.revision,
            payload_json=rule.model_dump_json(),
        )
        self._add(row, f"Policy rule already exists: {rule.id}")
        return rule

    def get_policy_rule(self, rule_id: str) -> PolicyRule | None:
        with Session(self.engine) as db:
            row = db.get(PolicyRuleRow, rule_id)
            return None if row is None else PolicyRule.model_validate_json(row.payload_json)

    def list_policy_rules(self, server_id: str, tool_name: str) -> list[PolicyRule]:
        with Session(self.engine) as db:
            rows = db.scalars(
                select(PolicyRuleRow)
                .where(
                    PolicyRuleRow.server_id == server_id,
                    PolicyRuleRow.tool_name == tool_name,
                )
                .order_by(PolicyRuleRow.id)
            ).all()
            return [PolicyRule.model_validate_json(row.payload_json) for row in rows]

    def update_policy_rule(
        self,
        rule_id: str,
        *,
        expected_revision: int,
        **changes: Any,
    ) -> PolicyRule:
        allowed = set(PolicyRule.model_fields) - {"id", "revision"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Unsupported PolicyRule changes: {sorted(unknown)}")
        with Session(self.engine) as db:
            row = db.get(PolicyRuleRow, rule_id)
            if row is None:
                raise RecordNotFoundError(f"Policy rule not found: {rule_id}")
            current = PolicyRule.model_validate_json(row.payload_json)
            candidate = PolicyRule.model_validate(
                {
                    **current.model_dump(mode="python"),
                    **changes,
                    "revision": expected_revision + 1,
                }
            )
            result = db.execute(
                update(PolicyRuleRow)
                .where(
                    PolicyRuleRow.id == rule_id,
                    PolicyRuleRow.revision == expected_revision,
                )
                .values(
                    server_id=candidate.server_id,
                    tool_name=candidate.tool_name,
                    effect=candidate.effect,
                    enabled=candidate.enabled,
                    revision=candidate.revision,
                    payload_json=candidate.model_dump_json(),
                )
            )
            if result.rowcount != 1:
                db.rollback()
                raise RevisionConflictError(
                    f"Policy rule revision conflict: {rule_id} expected {expected_revision}"
                )
            db.commit()
            return candidate

    def delete_policy_rule(
        self,
        rule_id: str,
        *,
        expected_revision: int,
    ) -> PolicyRule:
        with Session(self.engine) as db:
            row = db.get(PolicyRuleRow, rule_id)
            if row is None:
                raise RecordNotFoundError(f"Policy rule not found: {rule_id}")
            current = PolicyRule.model_validate_json(row.payload_json)
            result = db.execute(
                delete(PolicyRuleRow).where(
                    PolicyRuleRow.id == rule_id,
                    PolicyRuleRow.revision == expected_revision,
                )
            )
            if result.rowcount != 1:
                db.rollback()
                raise RevisionConflictError(
                    f"Policy rule revision conflict: {rule_id} expected {expected_revision}"
                )
            db.commit()
            return current

    def create_confirmation(self, confirmation: Confirmation) -> Confirmation:
        row = ConfirmationRow(
            id=confirmation.id,
            server_id=confirmation.server_id,
            tool_name=confirmation.tool_name,
            status=confirmation.status,
            decision=confirmation.decision,
            idempotency_key_hash=confirmation.idempotency_key_hash,
            expires_at=confirmation.expires_at,
            revision=confirmation.revision,
            payload_json=confirmation.model_dump_json(),
        )
        self._add(row, f"Confirmation already exists: {confirmation.id}")
        return confirmation

    def get_confirmation(self, confirmation_id: str) -> Confirmation | None:
        with Session(self.engine) as db:
            row = db.get(ConfirmationRow, confirmation_id)
            return (
                None
                if row is None
                else Confirmation.model_validate_json(row.payload_json)
            )

    def list_confirmations(
        self,
        *,
        session_id: str | None = None,
        status: str | None = None,
    ) -> list[Confirmation]:
        with Session(self.engine) as db:
            statement = select(ConfirmationRow)
            if status is not None:
                statement = statement.where(ConfirmationRow.status == status)
            rows = db.scalars(
                statement.order_by(ConfirmationRow.expires_at, ConfirmationRow.id)
            ).all()
            confirmations = [
                Confirmation.model_validate_json(row.payload_json) for row in rows
            ]
            if session_id is not None:
                confirmations = [
                    item for item in confirmations if item.session_id == session_id
                ]
            return confirmations

    def invalidate_confirmation(
        self,
        confirmation_id: str,
        *,
        expected_revision: int,
        status: str = "invalidated",
        now: datetime | None = None,
    ) -> Confirmation:
        if status not in {"invalidated", "expired"}:
            raise ValueError("Confirmation invalidation status is unsupported")
        decided_at = now or datetime.now(UTC)
        with Session(self.engine) as db:
            row = db.get(ConfirmationRow, confirmation_id)
            if row is None:
                raise RecordNotFoundError(f"Confirmation not found: {confirmation_id}")
            current = Confirmation.model_validate_json(row.payload_json)
            if current.status == status:
                return current
            if current.status not in {"pending", "approved"}:
                raise RevisionConflictError(
                    f"Confirmation cannot become {status}: {current.status}"
                )
            candidate = Confirmation.model_validate(
                {
                    **current.model_dump(mode="python"),
                    "decision": None,
                    "status": status,
                    "idempotency_key_hash": None,
                    "execution_idempotency_key_hash": None,
                    "consumed_at": None,
                    "decided_at": decided_at,
                    "revision": expected_revision + 1,
                }
            )
            result = db.execute(
                update(ConfirmationRow)
                .where(
                    ConfirmationRow.id == confirmation_id,
                    ConfirmationRow.revision == expected_revision,
                    ConfirmationRow.status.in_(("pending", "approved")),
                )
                .values(
                    status=status,
                    decision=None,
                    idempotency_key_hash=None,
                    revision=candidate.revision,
                    payload_json=candidate.model_dump_json(),
                )
            )
            if result.rowcount != 1:
                db.rollback()
                raise RevisionConflictError(
                    f"Confirmation invalidation conflict: {confirmation_id}"
                )
            db.commit()
            return candidate

    def expire_pending_confirmations(
        self,
        *,
        now: datetime | None = None,
        include_unexpired: bool = False,
    ) -> list[Confirmation]:
        cutoff = now or datetime.now(UTC)
        pending = self.list_confirmations(status="pending")
        expired: list[Confirmation] = []
        for confirmation in pending:
            if not include_unexpired and confirmation.expires_at > cutoff:
                continue
            try:
                expired.append(
                    self.invalidate_confirmation(
                        confirmation.id,
                        expected_revision=confirmation.revision,
                        status="expired",
                        now=cutoff,
                    )
                )
            except RevisionConflictError:
                continue
        return expired

    def decide_confirmation(
        self,
        confirmation_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
        decision: ConfirmationDecision,
    ) -> Confirmation:
        if not idempotency_key or len(idempotency_key) > 1_000:
            raise ValueError("Confirmation idempotency key must be non-empty and bounded")
        key_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        now = datetime.now(UTC)
        with Session(self.engine) as db:
            row = db.get(ConfirmationRow, confirmation_id)
            if row is None:
                raise RecordNotFoundError(f"Confirmation not found: {confirmation_id}")
            current = Confirmation.model_validate_json(row.payload_json)
            if current.status != "pending":
                if (
                    expected_revision == current.revision - 1
                    and current.idempotency_key_hash == key_hash
                    and current.decision == decision
                ):
                    return current
                raise RevisionConflictError(
                    f"Confirmation already reached terminal state: {current.status}"
                )
            if current.expires_at <= now:
                candidate = Confirmation.model_validate(
                    {
                        **current.model_dump(mode="python"),
                        "status": "expired",
                        "decided_at": now,
                        "revision": expected_revision + 1,
                    }
                )
                result = db.execute(
                    update(ConfirmationRow)
                    .where(
                        ConfirmationRow.id == confirmation_id,
                        ConfirmationRow.revision == expected_revision,
                        ConfirmationRow.status == "pending",
                    )
                    .values(
                        status=candidate.status,
                        revision=candidate.revision,
                        payload_json=candidate.model_dump_json(),
                    )
                )
                if result.rowcount != 1:
                    db.rollback()
                    raise RevisionConflictError(
                        f"Confirmation expiry conflict: {confirmation_id}"
                    )
                db.commit()
                return candidate
            status = "cancelled" if decision == "cancel" else "approved"
            candidate = Confirmation.model_validate(
                {
                    **current.model_dump(mode="python"),
                    "decision": decision,
                    "status": status,
                    "idempotency_key_hash": key_hash,
                    "decided_at": now,
                    "revision": expected_revision + 1,
                }
            )
            result = db.execute(
                update(ConfirmationRow)
                .where(
                    ConfirmationRow.id == confirmation_id,
                    ConfirmationRow.revision == expected_revision,
                    ConfirmationRow.status == "pending",
                )
                .values(
                    status=candidate.status,
                    decision=candidate.decision,
                    idempotency_key_hash=candidate.idempotency_key_hash,
                    revision=candidate.revision,
                    payload_json=candidate.model_dump_json(),
                )
            )
            if result.rowcount != 1:
                db.rollback()
                latest = self.get_confirmation(confirmation_id)
                if (
                    latest is not None
                    and latest.idempotency_key_hash == key_hash
                    and latest.decision == decision
                    and latest.status != "pending"
                    and expected_revision == latest.revision - 1
                ):
                    return latest
                raise RevisionConflictError(
                    f"Confirmation revision conflict: {confirmation_id} "
                    f"expected {expected_revision}"
                )
            db.commit()
            return candidate

    def consume_confirmation_execution(
        self,
        confirmation_id: str,
        *,
        execution_idempotency_key: str,
        now: datetime | None = None,
    ) -> Confirmation:
        """Atomically claim a one-shot approval for exactly one execution."""

        if not execution_idempotency_key or len(execution_idempotency_key) > 1_000:
            raise ConfirmationExecutionError("confirmation_execution_key_invalid")
        key_hash = hashlib.sha256(execution_idempotency_key.encode("utf-8")).hexdigest()
        consumed_at = now or datetime.now(UTC)
        with Session(self.engine) as db:
            row = db.get(ConfirmationRow, confirmation_id)
            if row is None:
                raise ConfirmationExecutionError("confirmation_not_found")
            current = Confirmation.model_validate_json(row.payload_json)
            if current.expires_at <= consumed_at:
                raise ConfirmationExecutionError("confirmation_expired")
            if current.status == "consumed":
                raise ConfirmationExecutionError("confirmation_consumed")
            if current.status != "approved":
                raise ConfirmationExecutionError("confirmation_not_approved")
            candidate = current.model_copy(
                update={
                    "status": "consumed",
                    "execution_idempotency_key_hash": key_hash,
                    "consumed_at": consumed_at,
                    "revision": current.revision + 1,
                }
            )
            result = db.execute(
                update(ConfirmationRow)
                .where(
                    ConfirmationRow.id == confirmation_id,
                    ConfirmationRow.revision == current.revision,
                    ConfirmationRow.status == "approved",
                )
                .values(
                    status="consumed",
                    revision=candidate.revision,
                    payload_json=candidate.model_dump_json(),
                )
            )
            if result.rowcount != 1:
                db.rollback()
                raise ConfirmationExecutionError("confirmation_consumed")
            db.commit()
            return candidate

    def create_execution_permit(self, permit: ExecutionPermit) -> ExecutionPermit:
        row = ExecutionPermitRow(
            id=permit.id,
            confirmation_id=permit.confirmation_id,
            request_hash=permit.request_hash,
            expires_at=permit.expires_at,
            consumed_at=permit.consumed_at,
            payload_json=permit.model_dump_json(),
        )
        self._add(row, f"Execution permit already exists: {permit.id}")
        return permit

    def get_execution_permit(self, permit_id: str) -> ExecutionPermit | None:
        with Session(self.engine) as db:
            row = db.get(ExecutionPermitRow, permit_id)
            return (
                None
                if row is None
                else ExecutionPermit.model_validate_json(row.payload_json)
            )

    def consume_execution_permit(
        self,
        permit_id: str,
        *,
        expected: dict[str, Any],
        now: datetime | None = None,
    ) -> ExecutionPermit:
        """Validate every binding and atomically consume a live permit."""

        consumed_at = now or datetime.now(UTC)
        with Session(self.engine) as db:
            row = db.get(ExecutionPermitRow, permit_id)
            if row is None:
                raise ExecutionPermitError("permit_not_found")
            current = ExecutionPermit.model_validate_json(row.payload_json)
            if current.expires_at <= consumed_at:
                raise ExecutionPermitError("permit_expired")
            if current.consumed_at is not None:
                raise ExecutionPermitError("permit_consumed")
            if current.decision != "allow" or any(
                getattr(current, field, None) != value
                for field, value in expected.items()
            ):
                raise ExecutionPermitError("permit_binding_mismatch")
            candidate = current.model_copy(update={"consumed_at": consumed_at})
            result = db.execute(
                update(ExecutionPermitRow)
                .where(
                    ExecutionPermitRow.id == permit_id,
                    ExecutionPermitRow.consumed_at.is_(None),
                    ExecutionPermitRow.expires_at > consumed_at,
                )
                .values(
                    consumed_at=consumed_at,
                    payload_json=candidate.model_dump_json(),
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                db.rollback()
                raise ExecutionPermitError("permit_consumed")
            db.commit()
            return candidate

    def create_skill(self, skill: SkillRecord) -> SkillRecord:
        row = SkillRecordRow(
            name=skill.name,
            enabled=skill.enabled,
            load_state=skill.load_state,
            revision=skill.revision,
            payload_json=skill.model_dump_json(),
        )
        self._add(row, f"Skill already exists: {skill.name}")
        return skill

    def get_skill(self, name: str) -> SkillRecord | None:
        with Session(self.engine) as db:
            row = db.get(SkillRecordRow, name)
            return None if row is None else SkillRecord.model_validate_json(row.payload_json)

    def update_skill(
        self,
        name: str,
        *,
        expected_revision: int,
        **changes: Any,
    ) -> SkillRecord:
        allowed = set(SkillRecord.model_fields) - {"name", "revision"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Unsupported SkillRecord changes: {sorted(unknown)}")
        with Session(self.engine) as db:
            row = db.get(SkillRecordRow, name)
            if row is None:
                raise RecordNotFoundError(f"Skill not found: {name}")
            current = SkillRecord.model_validate_json(row.payload_json)
            candidate = SkillRecord.model_validate(
                {
                    **current.model_dump(mode="python"),
                    **changes,
                    "revision": expected_revision + 1,
                }
            )
            result = db.execute(
                update(SkillRecordRow)
                .where(
                    SkillRecordRow.name == name,
                    SkillRecordRow.revision == expected_revision,
                )
                .values(
                    enabled=candidate.enabled,
                    load_state=candidate.load_state,
                    revision=candidate.revision,
                    payload_json=candidate.model_dump_json(),
                )
            )
            if result.rowcount != 1:
                db.rollback()
                raise RevisionConflictError(
                    f"Skill revision conflict: {name} expected {expected_revision}"
                )
            db.commit()
            return candidate

    def append_audit_event(self, audit_event: AuditEvent) -> AuditEvent:
        row = AuditEventRow(
            event_id=audit_event.event_id,
            actor=audit_event.actor,
            action=audit_event.action,
            target=audit_event.target,
            decision=audit_event.decision,
            created_at=audit_event.created_at,
            payload_json=audit_event.model_dump_json(),
        )
        self._add(row, f"Audit event already exists: {audit_event.event_id}")
        for sink in tuple(self._audit_sinks):
            sink(audit_event)
        return audit_event

    def add_audit_sink(self, sink: Callable[[AuditEvent], None]) -> None:
        """Attach a production observer after the durable audit write succeeds."""
        if sink not in self._audit_sinks:
            self._audit_sinks.append(sink)

    def list_audit_events(self) -> list[AuditEvent]:
        with Session(self.engine) as db:
            rows = db.scalars(
                select(AuditEventRow).order_by(
                    AuditEventRow.created_at,
                    AuditEventRow.event_id,
                )
            ).all()
            return [AuditEvent.model_validate_json(row.payload_json) for row in rows]

    def _add(self, row: CapabilityBase, duplicate_message: str) -> None:
        with Session(self.engine) as db:
            try:
                db.add(row)
                db.commit()
            except IntegrityError as exc:
                db.rollback()
                raise RecordAlreadyExistsError(duplicate_message) from exc
