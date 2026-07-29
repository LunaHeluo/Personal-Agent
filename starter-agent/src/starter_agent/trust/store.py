from __future__ import annotations

from pathlib import Path
from typing import Any, TypeVar

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
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

from starter_agent.trust.models import (
    EvalAssertionResult,
    EvalCase,
    EvalCaseResult,
    EvalFailureCluster,
    EvalFixture,
    EvalMetric,
    EvalReleaseGate,
    EvalRun,
    EvalSuite,
    HumanReview,
    JudgeResult,
    JudgeRubric,
    SmokeRun,
    TrustTraceEvent,
)


class TrustStoreError(RuntimeError):
    pass


class RecordAlreadyExistsError(TrustStoreError):
    pass


class PayloadConflictError(TrustStoreError):
    pass


class TrustBase(DeclarativeBase):
    pass


class EvalSuiteRow(TrustBase):
    __tablename__ = "trust_eval_suites"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    version: Mapped[str] = mapped_column(String(120), index=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), index=True)
    payload_json: Mapped[str] = mapped_column(Text)


class EvalFixtureRow(TrustBase):
    __tablename__ = "trust_eval_fixtures"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    fixture_type: Mapped[str] = mapped_column(String(80), index=True)
    version: Mapped[str] = mapped_column(String(120), index=True)
    manifest_hash: Mapped[str] = mapped_column(String(64), index=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    source_ref: Mapped[str] = mapped_column(String(500))
    payload_json: Mapped[str] = mapped_column(Text)


class EvalCaseRow(TrustBase):
    __tablename__ = "trust_eval_cases"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    suite_id: Mapped[str] = mapped_column(
        String(160),
        ForeignKey("trust_eval_suites.id"),
        index=True,
    )
    version: Mapped[str] = mapped_column(String(120), index=True)
    layer: Mapped[str] = mapped_column(String(120), index=True)
    safety_level: Mapped[str] = mapped_column(String(80), index=True)
    payload_json: Mapped[str] = mapped_column(Text)


class EvalRunRow(TrustBase):
    __tablename__ = "trust_eval_runs"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    suite_id: Mapped[str] = mapped_column(
        String(160),
        ForeignKey("trust_eval_suites.id"),
        index=True,
    )
    run_type: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    started_at: Mapped[Any] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[Any | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    code_version: Mapped[str] = mapped_column(String(160), index=True)
    code_dirty: Mapped[bool] = mapped_column(Boolean, index=True)
    prompt_version: Mapped[str] = mapped_column(String(160), index=True)
    skill_version: Mapped[str] = mapped_column(String(160), index=True)
    tool_schema_version: Mapped[str] = mapped_column(String(160), index=True)
    policy_version: Mapped[str] = mapped_column(String(160), index=True)
    fixture_manifest_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    payload_json: Mapped[str] = mapped_column(Text)


class EvalCaseResultRow(TrustBase):
    __tablename__ = "trust_eval_case_results"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(160),
        ForeignKey("trust_eval_runs.id"),
        index=True,
    )
    case_id: Mapped[str] = mapped_column(
        String(160),
        ForeignKey("trust_eval_cases.id"),
        index=True,
    )
    status: Mapped[str] = mapped_column(String(40), index=True)
    session_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    turn_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    payload_json: Mapped[str] = mapped_column(Text)


class EvalAssertionResultRow(TrustBase):
    __tablename__ = "trust_eval_assertion_results"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(160),
        ForeignKey("trust_eval_runs.id"),
        index=True,
    )
    case_result_id: Mapped[str] = mapped_column(
        String(160),
        ForeignKey("trust_eval_case_results.id"),
        index=True,
    )
    assertion_id: Mapped[str] = mapped_column(String(300), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    payload_json: Mapped[str] = mapped_column(Text)


class EvalMetricRow(TrustBase):
    __tablename__ = "trust_eval_metrics"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(160),
        ForeignKey("trust_eval_runs.id"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(160), index=True)
    unit: Mapped[str] = mapped_column(String(80), index=True)
    missing: Mapped[bool] = mapped_column(Boolean, index=True)
    payload_json: Mapped[str] = mapped_column(Text)


class EvalFailureClusterRow(TrustBase):
    __tablename__ = "trust_eval_failure_clusters"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(160),
        ForeignKey("trust_eval_runs.id"),
        index=True,
    )
    cluster_key: Mapped[str] = mapped_column(String(200), index=True)
    title: Mapped[str] = mapped_column(String(200))
    payload_json: Mapped[str] = mapped_column(Text)


class EvalReleaseGateRow(TrustBase):
    __tablename__ = "trust_eval_release_gates"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(160),
        ForeignKey("trust_eval_runs.id"),
        unique=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(40), index=True)
    safety_blocking: Mapped[bool] = mapped_column(Boolean, index=True)
    payload_json: Mapped[str] = mapped_column(Text)


class TrustTraceEventRow(TrustBase):
    __tablename__ = "trust_trace_events"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    eval_run_id: Mapped[str | None] = mapped_column(
        String(160),
        nullable=True,
        index=True,
    )
    case_id: Mapped[str | None] = mapped_column(
        String(160),
        nullable=True,
        index=True,
    )
    session_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    turn_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    model_request_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    policy_decision_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    approval_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    child_run_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    parent_event_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(80), index=True)
    occurred_at: Mapped[Any] = mapped_column(DateTime(timezone=True), index=True)
    payload_hash: Mapped[str] = mapped_column(String(64), index=True)
    source_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text)


Index(
    "ix_trust_trace_events_run_case_time",
    TrustTraceEventRow.eval_run_id,
    TrustTraceEventRow.case_id,
    TrustTraceEventRow.occurred_at,
)


class SmokeRunRow(TrustBase):
    __tablename__ = "trust_smoke_runs"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(160),
        ForeignKey("trust_eval_runs.id"),
        unique=True,
        index=True,
    )
    source_url_hash: Mapped[str] = mapped_column(String(64), index=True)
    payload_json: Mapped[str] = mapped_column(Text)


class JudgeRubricRow(TrustBase):
    __tablename__ = "trust_judge_rubrics"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    suite_id: Mapped[str] = mapped_column(String(160), index=True)
    version: Mapped[str] = mapped_column(String(120), index=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), index=True)
    payload_json: Mapped[str] = mapped_column(Text)


class JudgeResultRow(TrustBase):
    __tablename__ = "trust_judge_results"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(160), index=True)
    case_result_id: Mapped[str] = mapped_column(String(160), index=True)
    rubric_id: Mapped[str] = mapped_column(String(160), index=True)
    provider: Mapped[str] = mapped_column(String(120), index=True)
    model: Mapped[str] = mapped_column(String(160), index=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), index=True)
    payload_json: Mapped[str] = mapped_column(Text)


class HumanReviewRow(TrustBase):
    __tablename__ = "trust_human_reviews"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(160), index=True)
    case_id: Mapped[str] = mapped_column(String(160), index=True)
    case_result_id: Mapped[str] = mapped_column(String(160), index=True)
    reviewer: Mapped[str] = mapped_column(String(160), index=True)
    conclusion: Mapped[str] = mapped_column(String(120), index=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), index=True)
    payload_json: Mapped[str] = mapped_column(Text)


T = TypeVar("T")


class TrustStore:
    def __init__(self, database_url: str, project_root: Path | str) -> None:
        root = Path(project_root)
        if database_url == "sqlite:///:memory:":
            self.engine = create_engine(
                database_url,
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
        elif database_url.startswith("sqlite:///"):
            relative = database_url.removeprefix("sqlite:///")
            database_path = Path(relative)
            if not database_path.is_absolute():
                database_path = root / database_path
            database_path.parent.mkdir(parents=True, exist_ok=True)
            self.engine = create_engine(f"sqlite:///{database_path}")
        else:
            self.engine = create_engine(database_url)

        if self.engine.dialect.name == "sqlite":

            @event.listens_for(self.engine, "connect")
            def _configure_sqlite(dbapi_connection, _record) -> None:
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

        TrustBase.metadata.create_all(self.engine)

    def close(self) -> None:
        self.engine.dispose()

    def create_suite(self, suite: EvalSuite) -> EvalSuite:
        self._add(
            EvalSuiteRow(
                id=suite.id,
                name=suite.name,
                version=suite.version,
                created_at=suite.created_at,
                payload_json=suite.model_dump_json(),
            ),
            f"Eval suite already exists: {suite.id}",
        )
        return suite

    def get_suite(self, suite_id: str) -> EvalSuite | None:
        return self._get(EvalSuiteRow, suite_id, EvalSuite)

    def list_suites(self, *, limit: int = 100) -> list[EvalSuite]:
        with Session(self.engine) as db:
            rows = db.scalars(
                select(EvalSuiteRow).order_by(EvalSuiteRow.created_at, EvalSuiteRow.id).limit(limit)
            ).all()
            return [EvalSuite.model_validate_json(row.payload_json) for row in rows]

    def create_fixture(self, fixture: EvalFixture) -> EvalFixture:
        self._add(
            EvalFixtureRow(
                id=fixture.id,
                fixture_type=fixture.fixture_type,
                version=fixture.version,
                manifest_hash=fixture.manifest_hash,
                content_hash=fixture.content_hash,
                source_ref=fixture.source_ref,
                payload_json=fixture.model_dump_json(),
            ),
            f"Eval fixture already exists: {fixture.id}",
        )
        return fixture

    def get_fixture(self, fixture_id: str) -> EvalFixture | None:
        return self._get(EvalFixtureRow, fixture_id, EvalFixture)

    def create_case(self, case: EvalCase) -> EvalCase:
        self._add(
            EvalCaseRow(
                id=case.id,
                suite_id=case.suite_id,
                version=case.version,
                layer=case.layer,
                safety_level=case.safety_level,
                payload_json=case.model_dump_json(),
            ),
            f"Eval case already exists: {case.id}",
        )
        return case

    def get_case(self, case_id: str) -> EvalCase | None:
        return self._get(EvalCaseRow, case_id, EvalCase)

    def list_cases(
        self,
        *,
        suite_id: str | None = None,
        limit: int = 500,
    ) -> list[EvalCase]:
        with Session(self.engine) as db:
            statement = select(EvalCaseRow)
            if suite_id is not None:
                statement = statement.where(EvalCaseRow.suite_id == suite_id)
            rows = db.scalars(
                statement.order_by(EvalCaseRow.suite_id, EvalCaseRow.id).limit(limit)
            ).all()
            return [EvalCase.model_validate_json(row.payload_json) for row in rows]

    def create_run(self, run: EvalRun) -> EvalRun:
        self._add(
            EvalRunRow(
                id=run.id,
                suite_id=run.suite_id,
                run_type=run.run_type,
                status=run.status,
                started_at=run.started_at,
                completed_at=run.completed_at,
                code_version=run.code_version,
                code_dirty=run.code_dirty,
                prompt_version=run.prompt_version,
                skill_version=run.skill_version,
                tool_schema_version=run.tool_schema_version,
                policy_version=run.policy_version,
                fixture_manifest_hash=run.fixture_manifest_hash,
                payload_json=run.model_dump_json(),
            ),
            f"Eval run already exists: {run.id}",
        )
        return run

    def get_run(self, run_id: str) -> EvalRun | None:
        return self._get(EvalRunRow, run_id, EvalRun)

    def update_run_status(
        self,
        run_id: str,
        *,
        status: str,
        completed_at: Any | None = None,
    ) -> EvalRun:
        with Session(self.engine) as db:
            row = db.get(EvalRunRow, run_id)
            if row is None:
                raise TrustStoreError(f"Eval run not found: {run_id}")
            current = EvalRun.model_validate_json(row.payload_json)
            candidate = current.model_copy(
                update={"status": status, "completed_at": completed_at},
            )
            db.execute(
                update(EvalRunRow)
                .where(EvalRunRow.id == run_id)
                .values(
                    status=status,
                    completed_at=completed_at,
                    payload_json=candidate.model_dump_json(),
                )
            )
            db.commit()
            return candidate

    def list_runs(self, *, run_type: str | None = None, limit: int = 100) -> list[EvalRun]:
        with Session(self.engine) as db:
            statement = select(EvalRunRow)
            if run_type is not None:
                statement = statement.where(EvalRunRow.run_type == run_type)
            rows = db.scalars(
                statement.order_by(EvalRunRow.started_at, EvalRunRow.id).limit(limit)
            ).all()
            return [EvalRun.model_validate_json(row.payload_json) for row in rows]

    def create_case_result(self, result: EvalCaseResult) -> EvalCaseResult:
        return self._insert_idempotent(
            EvalCaseResultRow(
                id=result.id,
                run_id=result.run_id,
                case_id=result.case_id,
                status=result.status,
                session_id=result.session_id,
                turn_id=result.turn_id,
                payload_json=result.model_dump_json(),
            ),
            EvalCaseResult,
        )

    def list_case_results(self, *, run_id: str) -> list[EvalCaseResult]:
        return self._list(
            EvalCaseResultRow,
            EvalCaseResult,
            EvalCaseResultRow.run_id == run_id,
            order_by=(EvalCaseResultRow.case_id, EvalCaseResultRow.id),
        )

    def create_assertion_result(
        self,
        result: EvalAssertionResult,
    ) -> EvalAssertionResult:
        self._add(
            EvalAssertionResultRow(
                id=result.id,
                run_id=result.run_id,
                case_result_id=result.case_result_id,
                assertion_id=result.assertion_id,
                status=result.status,
                payload_json=result.model_dump_json(),
            ),
            f"Eval assertion result already exists: {result.id}",
        )
        return result

    def list_assertion_results(
        self,
        *,
        case_result_id: str,
    ) -> list[EvalAssertionResult]:
        return self._list(
            EvalAssertionResultRow,
            EvalAssertionResult,
            EvalAssertionResultRow.case_result_id == case_result_id,
            order_by=(
                EvalAssertionResultRow.assertion_id,
                EvalAssertionResultRow.id,
            ),
        )

    def create_metric(self, metric: EvalMetric) -> EvalMetric:
        self._add(
            EvalMetricRow(
                id=metric.id,
                run_id=metric.run_id,
                name=metric.name,
                unit=metric.unit,
                missing=metric.missing,
                payload_json=metric.model_dump_json(),
            ),
            f"Eval metric already exists: {metric.id}",
        )
        return metric

    def list_metrics(self, *, run_id: str) -> list[EvalMetric]:
        return self._list(
            EvalMetricRow,
            EvalMetric,
            EvalMetricRow.run_id == run_id,
            order_by=(EvalMetricRow.name, EvalMetricRow.id),
        )

    def create_failure_cluster(
        self,
        cluster: EvalFailureCluster,
    ) -> EvalFailureCluster:
        self._add(
            EvalFailureClusterRow(
                id=cluster.id,
                run_id=cluster.run_id,
                cluster_key=cluster.cluster_key,
                title=cluster.title,
                payload_json=cluster.model_dump_json(),
            ),
            f"Eval failure cluster already exists: {cluster.id}",
        )
        return cluster

    def list_failure_clusters(self, *, run_id: str) -> list[EvalFailureCluster]:
        return self._list(
            EvalFailureClusterRow,
            EvalFailureCluster,
            EvalFailureClusterRow.run_id == run_id,
            order_by=(EvalFailureClusterRow.cluster_key, EvalFailureClusterRow.id),
        )

    def create_release_gate(self, gate: EvalReleaseGate) -> EvalReleaseGate:
        self._add(
            EvalReleaseGateRow(
                id=gate.id,
                run_id=gate.run_id,
                status=gate.status,
                safety_blocking=gate.safety_blocking,
                payload_json=gate.model_dump_json(),
            ),
            f"Eval release gate already exists: {gate.id}",
        )
        return gate

    def get_release_gate(self, run_id: str) -> EvalReleaseGate | None:
        with Session(self.engine) as db:
            row = db.scalars(
                select(EvalReleaseGateRow).where(EvalReleaseGateRow.run_id == run_id)
            ).first()
            return (
                None
                if row is None
                else EvalReleaseGate.model_validate_json(row.payload_json)
            )

    def append_trace_event(self, event: TrustTraceEvent) -> TrustTraceEvent:
        return self._insert_idempotent(
            TrustTraceEventRow(
                id=event.id,
                eval_run_id=event.eval_run_id,
                case_id=event.case_id,
                session_id=event.session_id,
                turn_id=event.turn_id,
                model_request_id=event.model_request_id,
                tool_call_id=event.tool_call_id,
                policy_decision_id=event.policy_decision_id,
                approval_id=event.approval_id,
                child_run_id=event.child_run_id,
                parent_event_id=event.parent_event_id,
                event_type=event.event_type,
                status=event.status,
                occurred_at=event.occurred_at,
                payload_hash=event.payload_hash,
                source_ref=event.source_ref,
                payload_json=event.model_dump_json(),
            ),
            TrustTraceEvent,
        )

    def list_trace_events(
        self,
        *,
        eval_run_id: str | None = None,
        case_id: str | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
        tool_call_id: str | None = None,
        limit: int = 500,
    ) -> list[TrustTraceEvent]:
        with Session(self.engine) as db:
            statement = select(TrustTraceEventRow)
            if eval_run_id is not None:
                statement = statement.where(TrustTraceEventRow.eval_run_id == eval_run_id)
            if case_id is not None:
                statement = statement.where(TrustTraceEventRow.case_id == case_id)
            if session_id is not None:
                statement = statement.where(TrustTraceEventRow.session_id == session_id)
            if turn_id is not None:
                statement = statement.where(TrustTraceEventRow.turn_id == turn_id)
            if tool_call_id is not None:
                statement = statement.where(TrustTraceEventRow.tool_call_id == tool_call_id)
            rows = db.scalars(
                statement.order_by(
                    TrustTraceEventRow.occurred_at,
                    TrustTraceEventRow.id,
                ).limit(limit)
            ).all()
            return [TrustTraceEvent.model_validate_json(row.payload_json) for row in rows]

    def create_smoke_run(self, run: SmokeRun) -> SmokeRun:
        self._add(
            SmokeRunRow(
                id=run.id,
                run_id=run.run_id,
                source_url_hash=run.source_url_hash,
                payload_json=run.model_dump_json(),
            ),
            f"Smoke run already exists: {run.id}",
        )
        return run

    def get_smoke_run(self, smoke_run_id: str) -> SmokeRun | None:
        return self._get(SmokeRunRow, smoke_run_id, SmokeRun)

    def list_smoke_runs(self, *, limit: int = 100) -> list[SmokeRun]:
        with Session(self.engine) as db:
            rows = db.scalars(select(SmokeRunRow).limit(limit)).all()
            return [SmokeRun.model_validate_json(row.payload_json) for row in rows]

    def create_judge_rubric(self, rubric: JudgeRubric) -> JudgeRubric:
        self._add(
            JudgeRubricRow(
                id=rubric.id,
                suite_id=rubric.suite_id,
                version=rubric.version,
                created_at=rubric.created_at,
                payload_json=rubric.model_dump_json(),
            ),
            f"Judge rubric already exists: {rubric.id}",
        )
        return rubric

    def get_judge_rubric(self, rubric_id: str) -> JudgeRubric | None:
        return self._get(JudgeRubricRow, rubric_id, JudgeRubric)

    def create_judge_result(self, result: JudgeResult) -> JudgeResult:
        self._add(
            JudgeResultRow(
                id=result.id,
                run_id=result.run_id,
                case_result_id=result.case_result_id,
                rubric_id=result.rubric_id,
                provider=result.provider,
                model=result.model,
                created_at=result.created_at,
                payload_json=result.model_dump_json(),
            ),
            f"Judge result already exists: {result.id}",
        )
        return result

    def get_judge_result(self, result_id: str) -> JudgeResult | None:
        return self._get(JudgeResultRow, result_id, JudgeResult)

    def create_human_review(self, review: HumanReview) -> HumanReview:
        self._add(
            HumanReviewRow(
                id=review.id,
                run_id=review.run_id,
                case_id=review.case_id,
                case_result_id=review.case_result_id,
                reviewer=review.reviewer,
                conclusion=review.conclusion,
                created_at=review.created_at,
                payload_json=review.model_dump_json(),
            ),
            f"Human review already exists: {review.id}",
        )
        return review

    def list_human_reviews(self, *, run_id: str) -> list[HumanReview]:
        return self._list(
            HumanReviewRow,
            HumanReview,
            HumanReviewRow.run_id == run_id,
            order_by=(HumanReviewRow.created_at, HumanReviewRow.id),
        )

    def _get(self, row_type: type[Any], row_id: str, model_type: type[T]) -> T | None:
        with Session(self.engine) as db:
            row = db.get(row_type, row_id)
            return None if row is None else model_type.model_validate_json(row.payload_json)

    def _list(
        self,
        row_type: type[Any],
        model_type: type[T],
        *criteria: Any,
        order_by: tuple[Any, ...],
        limit: int = 500,
    ) -> list[T]:
        with Session(self.engine) as db:
            statement = select(row_type)
            for criterion in criteria:
                statement = statement.where(criterion)
            rows = db.scalars(statement.order_by(*order_by).limit(limit)).all()
            return [model_type.model_validate_json(row.payload_json) for row in rows]

    def _insert_idempotent(self, row: Any, model_type: type[T]) -> T:
        with Session(self.engine) as db:
            existing = db.get(type(row), row.id)
            if existing is not None:
                if existing.payload_json == row.payload_json:
                    return model_type.model_validate_json(existing.payload_json)
                raise PayloadConflictError(f"Payload conflict for Trust record: {row.id}")
            try:
                db.add(row)
                db.commit()
            except IntegrityError as exc:
                db.rollback()
                latest = db.get(type(row), row.id)
                if latest is not None and latest.payload_json == row.payload_json:
                    return model_type.model_validate_json(latest.payload_json)
                raise PayloadConflictError(
                    f"Payload conflict for Trust record: {row.id}"
                ) from exc
            return model_type.model_validate_json(row.payload_json)

    def _add(self, row: TrustBase, duplicate_message: str) -> None:
        with Session(self.engine) as db:
            try:
                db.add(row)
                db.commit()
            except IntegrityError as exc:
                db.rollback()
                raise RecordAlreadyExistsError(duplicate_message) from exc
