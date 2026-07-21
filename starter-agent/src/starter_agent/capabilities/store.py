from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
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
    event,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import StaticPool

from starter_agent.capabilities.models import (
    AuditEvent,
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
        ACTIVE_SNAPSHOT_INDEX.create(self.engine, checkfirst=True)

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
        tool_items = tuple(tools)
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

    def list_tools(self, snapshot_id: str) -> list[Tool]:
        with Session(self.engine) as db:
            rows = db.scalars(
                select(McpToolRow)
                .where(McpToolRow.snapshot_id == snapshot_id)
                .order_by(McpToolRow.upstream_name)
            ).all()
            return [Tool.model_validate_json(row.payload_json) for row in rows]

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
        return audit_event

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
