from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from starter_agent.capabilities.models import canonical_json_sha256
from starter_agent.delegation.store import ArtifactLink
from starter_agent.delegation.models import BudgetUsage, ResultEnvelope, RunOutcome

from starter_agent.delegation.dispatcher import Dispatcher
from starter_agent.delegation.service import DelegationService
from starter_agent.delegation.context import BuiltChildContext, RunContext


class PersistedChildAssembler:
    """Production adapter from a persisted claim to Task6's single Context Builder."""

    def __init__(self, *, registry, context_builder, authority_factory, reference_factory=None) -> None:
        self.registry = registry
        self.context_builder = context_builder
        self.authority_factory = authority_factory
        self.reference_factory = reference_factory or (lambda _claim: ())

    def __call__(self, claim):
        specialist = self.registry.resolve_pinned(
            claim.task.specialist_id,
            snapshot_hash=claim.task.specialist_snapshot_id,
        )
        contract = __import__(
            "starter_agent.delegation.models", fromlist=["TaskContract"]
        ).TaskContract(
            task_id=claim.task.id,
            parent_run_id=claim.task.parent_run_id,
            specialist_id=claim.task.specialist_id,
            goal=claim.task.goal,
            inputs=claim.task.inputs_ref_json,
            constraints=claim.task.constraints_json,
            requested_allowed_tools=claim.task.requested_allowed_tools,
            requested_deadline=claim.task.requested_deadline,
            requested_budget=claim.task.requested_budget,
            failure_behavior=claim.task.failure_behavior,
            idempotency_key=claim.task.idempotency_key,
            contract_version=claim.task.contract_version,
        )
        return self.context_builder.build(
            contract,
            specialist,
            self.authority_factory(claim, specialist),
            references=tuple(self.reference_factory(claim)),
        )


def build_child_result_envelope(*, task_id: str, child_run_id: str, status: str, output: dict[str, Any], usage: BudgetUsage, trace_ref: str, evidence: tuple[dict[str, Any], ...] = (), missing: tuple[str, ...] = (), conflicts: tuple[dict[str, Any], ...] = (), errors: tuple[dict[str, Any], ...] = ()) -> ResultEnvelope:
    """Bind adapter output to the persisted Child identity before acceptance."""
    return ResultEnvelope(status=status, output=output, evidence=evidence, missing=missing, conflicts=conflicts, errors=errors, usage=usage, child_run_id=child_run_id, task_id=task_id, trace_ref=trace_ref, idempotency_key=f"result:{child_run_id}")


class ChildRuntimeExecutor:
    """Connect persisted claims to ChildContextBuilder output and the shared Runtime."""

    def __init__(self, *, assemble, runtime, cancellation_probe=None, web_researcher=None, profile_evidence_analyst=None, checkpoint_store=None, artifact_store=None, run_store=None, acceptance_service=None, artifact_retention: timedelta = timedelta(days=14)) -> None:
        self.assemble = assemble
        self.runtime = runtime
        self.cancellation_probe = cancellation_probe
        self.web_researcher = web_researcher
        self.profile_evidence_analyst = profile_evidence_analyst
        self.checkpoint_store = checkpoint_store
        self.artifact_store = artifact_store
        self.run_store = run_store
        self.acceptance_service = acceptance_service
        self.artifact_retention = artifact_retention

    def _artifact_sink(self, claim, context):
        if self.artifact_store is None or self.run_store is None:
            return None

        async def persist(event: dict) -> None:
            source_ref = str(event["source_ref"])
            expires_at = datetime.now(UTC) + self.artifact_retention
            allowed = {
                "source_ref", "session_id", "turn_id", "tool_name", "content",
                "call_id", "server_id", "snapshot_id", "schema_hash",
                "requested_url", "final_url", "source_url", "content_sha256",
                "source_content_sha256", "truncation_summary",
            }
            stored = {key: value for key, value in event.items() if key in allowed}
            self.artifact_store.save_tool_artifact(
                **stored,
                parent_run_id=claim.parent.id,
                child_task_id=claim.task.id,
                child_run_id=claim.run.id,
                policy_decision_id=context.trace_context.policy_decision_id,
                approval_id=context.trace_context.approval_id,
                access_level="child_restricted",
                principal=claim.parent.principal,
                expires_at=expires_at,
            )
            link_id = "artifact-link:" + canonical_json_sha256(
                {"parent": claim.parent.id, "child": claim.run.id, "ref": source_ref}
            )[:48]
            is_profile_evidence = event.get("tool_name") == "retrieve_resume_evidence"
            evidence_refs = event.get("evidence_refs", ()) if is_profile_evidence else ()
            if not isinstance(evidence_refs, (list, tuple)):
                evidence_refs = ()
            links = evidence_refs or ({},)
            for evidence in links:
                if not isinstance(evidence, dict):
                    continue
                chunk_id = evidence.get("chunk_id")
                source_ref_for_chunk = evidence.get("source_ref", source_ref)
                if is_profile_evidence and (not isinstance(chunk_id, str) or not isinstance(source_ref_for_chunk, str)):
                    continue
                chunk_ref = source_ref_for_chunk if is_profile_evidence else source_ref
                chunk_link_id = link_id if not is_profile_evidence else "artifact-link:" + canonical_json_sha256({"parent": claim.parent.id, "child": claim.run.id, "ref": chunk_ref, "chunk": chunk_id})[:48]
                source_url_value = event.get("source_url") or event.get("final_url")
                if is_profile_evidence:
                    # Store only provenance identifiers for the individual RAG
                    # citation.  Resume text remains in the restricted knowledge
                    # system and never enters Parent/trace content.
                    self.artifact_store.save_tool_artifact(
                        source_ref=chunk_ref, session_id=context.session_id,
                        turn_id=context.turn_id, tool_name="retrieve_resume_evidence",
                        content=json.dumps({"chunk_id": chunk_id, "source_ref": chunk_ref, "document_id": evidence.get("document_id")}, separators=(",", ":")),
                        call_id=str(event.get("call_id")) if event.get("call_id") else None,
                        content_sha256=canonical_json_sha256({"chunk_id": chunk_id, "source_ref": chunk_ref}),
                        parent_run_id=claim.parent.id, child_task_id=claim.task.id,
                        child_run_id=claim.run.id, policy_decision_id=context.trace_context.policy_decision_id,
                        approval_id=context.trace_context.approval_id, access_level="child_restricted",
                        principal=claim.parent.principal, expires_at=expires_at,
                    )
                self.run_store.link_artifact(ArtifactLink(
                    id=chunk_link_id, parent_run_id=claim.parent.id,
                    child_run_id=claim.run.id, artifact_ref=chunk_ref,
                    kind="rag_evidence" if is_profile_evidence else "web_tool_result",
                    restricted=True, principal=claim.parent.principal,
                    source_url=(str(source_url_value) if source_url_value else None),
                    content_hash=(str(event.get("content_sha256") or event.get("source_content_sha256")) or None),
                    chunk_id=chunk_id if is_profile_evidence else (str(event.get("chunk_id")) if event.get("chunk_id") else None),
                    artifact_type="rag_evidence" if is_profile_evidence else None,
                    trace_ref=f"trace:child-run:{claim.run.id}",
                    knowledge_user_id=str(context.user_id) if is_profile_evidence and context.user_id else None,
                    knowledge_project_id=str(context.project_id) if is_profile_evidence and context.project_id else None,
                    knowledge_base_id=str(context.knowledge_base_id) if is_profile_evidence and context.knowledge_base_id else None,
                    document_id=str(evidence.get("document_id")) if is_profile_evidence and evidence.get("document_id") else None,
                    tool_name=str(event.get("tool_name")) if event.get("tool_name") else None,
                    policy_decision_id=context.trace_context.policy_decision_id,
                    approval_id=context.trace_context.approval_id,
                    created_at=datetime.now(UTC),
                ))
            if source_ref not in context.artifact_refs:
                context.artifact_refs.append(source_ref)

        return persist

    def _usage(self, context) -> BudgetUsage:
        consumed = context.budget.consumed
        provider_usages = list(context.provider_usages)
        estimated = any(
            item.get("cost_status") == "estimated"
            or item.get("cost_estimated") is True
            for item in provider_usages
        )
        price_versions = {
            str(item["price_version"])
            for item in provider_usages
            if item.get("price_version")
        }
        usage_sources = {
            str(item["usage_source"])
            for item in provider_usages
            if item.get("usage_source")
        }
        return BudgetUsage(
            **consumed.model_dump(mode="python"),
            estimated=estimated,
            cost_status=(
                "unknown"
                if context.budget.cost_unknown
                else "estimated" if estimated else "actual"
            ),
            price_version=(
                "+".join(sorted(price_versions)) if price_versions else "runtime"
            ),
            usage_source=(
                "+".join(sorted(usage_sources)) if usage_sources else "runtime"
            ),
        )

    def _envelope_outcome(self, claim, context, outcome, output: dict[str, Any]) -> RunOutcome:
        if (
            outcome.status not in {"succeeded", "partial"}
            and claim.task.failure_behavior == "allow_partial"
            and outcome.status in {"failed", "timed_out", "budget_exhausted"}
        ):
            reason = outcome.error_code or outcome.status
            output = dict(output)
            missing_output = list(output.get("missing") or [])
            errors_output = list(output.get("errors") or [])
            if {"reason": reason} not in missing_output:
                missing_output.append({"reason": reason})
            if {"code": reason} not in errors_output:
                errors_output.append({"code": reason})
            output["missing"] = missing_output
            output["errors"] = errors_output
            outcome = RunOutcome(
                disposition="completed",
                run_id=outcome.run_id,
                status="partial",
                output_ref=(
                    outcome.output_ref
                    or f"context-output:{outcome.run_id}:{context.context_version}"
                ),
                error_code=reason,
            )
        if outcome.status not in {"succeeded", "partial"} or self.artifact_store is None or self.run_store is None:
            return outcome
        missing = tuple(str(value) for value in output.get("missing", ()) if isinstance(value, str))
        conflicts = tuple(value for value in output.get("conflicts", ()) if isinstance(value, dict))
        errors = tuple(value for value in output.get("errors", ()) if isinstance(value, dict))
        evidence = tuple(value for value in output.get("evidence", ()) if isinstance(value, dict))
        envelope = build_child_result_envelope(task_id=claim.task.id, child_run_id=claim.run.id, status=outcome.status, output=output, usage=self._usage(context), trace_ref=f"trace:child-run:{claim.run.id}", evidence=evidence, missing=missing, conflicts=conflicts, errors=errors)
        ref = f"artifact:result-envelope:{claim.run.id}:{envelope.canonical_hash[:16]}"
        self.artifact_store.save_tool_artifact(source_ref=ref, session_id=context.session_id, turn_id=context.turn_id, tool_name="result_envelope", content=envelope.model_dump_json(), parent_run_id=claim.parent.id, child_task_id=claim.task.id, child_run_id=claim.run.id, access_level="child_restricted", principal=claim.parent.principal, expires_at=datetime.now(UTC) + self.artifact_retention)
        self.run_store.link_artifact(ArtifactLink(id=f"artifact-link:result-envelope:{claim.run.id}:{envelope.canonical_hash[:16]}", parent_run_id=claim.parent.id, child_run_id=claim.run.id, artifact_ref=ref, kind="result_envelope", restricted=True, principal=claim.parent.principal, artifact_type="result_envelope", trace_ref=envelope.trace_ref, created_at=datetime.now(UTC)))
        return outcome.model_copy(update={"result_envelope_ref": ref, "result_envelope_hash": envelope.canonical_hash})

    def accept_completed(self, outcome, *, now: datetime) -> None:
        if self.acceptance_service is None or outcome.result_envelope_ref is None:
            return
        child = self.run_store.get_child_run(outcome.run_id) if self.run_store is not None else None
        parent = self.run_store.get_parent(child.parent_run_id) if child is not None and self.run_store is not None else None
        artifact = (
            self.artifact_store.get_tool_artifact_for_principal(outcome.result_envelope_ref, principal=parent.principal)
            if self.artifact_store is not None and parent is not None else None
        )
        if artifact is None or not isinstance(artifact.get("content"), str):
            return
        envelope = ResultEnvelope.model_validate_json(artifact["content"])
        self.acceptance_service.validate_and_accept(envelope, now=now, child_run_id=envelope.child_run_id, envelope_ref=outcome.result_envelope_ref)

    def _web_researcher(self, *, artifact_sink=None):
        if self.web_researcher is not None:
            return self.web_researcher
        from starter_agent.delegation.specialists.job_web_researcher import JobWebResearcher
        sink = None
        if self.checkpoint_store is not None:
            sink = lambda _ref, payload: self.checkpoint_store.save_child_checkpoint(
                str(payload["child_run_id"]), payload
            )
        return JobWebResearcher(self.runtime, checkpoint_sink=sink, artifact_sink=artifact_sink)

    def _profile_evidence_analyst(self):
        if self.profile_evidence_analyst is not None:
            return self.profile_evidence_analyst
        from starter_agent.delegation.specialists.profile_evidence_analyst import ProfileEvidenceAnalyst
        return ProfileEvidenceAnalyst(self.runtime)

    async def __call__(self, claim):
        built = self.assemble(claim)
        if self.checkpoint_store is not None and claim.run.run_context_checkpoint_ref:
            payload = self.checkpoint_store.load_child_checkpoint_for_worker(
                claim.run.run_context_checkpoint_ref,
                parent_run_id=claim.parent.id, child_task_id=claim.task.id,
                child_run_id=claim.run.id, principal=claim.parent.principal,
            )
            restored = RunContext.from_checkpoint(dict(payload["run_context"]))
            restored.suspension_requested = False
            restored.suspension_checkpoint_ref = None
            restored.boundary_stop_reason = None
            built = BuiltChildContext(built.spec, restored, built.deadline, built.tool_view)
        if self.cancellation_probe is not None:
            built.context.cancellation_probe = lambda: self.cancellation_probe(claim)
        artifact_sink = self._artifact_sink(claim, built.context)
        task = getattr(claim, "task", None)
        if task is not None and task.specialist_id == "job_web_researcher":
            researcher = self._web_researcher(artifact_sink=artifact_sink)
            inputs = dict(task.inputs_ref_json)
            if hasattr(task, "failure_behavior"):
                inputs["failure_behavior"] = task.failure_behavior
            result = await researcher.run(built.spec, built.context, inputs)
            return self._envelope_outcome(claim, built.context, result.outcome, result.output)
        if task is not None and task.specialist_id == "profile_evidence_analyst":
            result = await self._profile_evidence_analyst().run(
                built.spec, built.context, dict(task.inputs_ref_json),
                on_tool_artifact=artifact_sink,
            )
            return self._envelope_outcome(claim, built.context, result.outcome, result.output)
        kwargs = {"on_tool_artifact": artifact_sink} if artifact_sink is not None else {}
        return await self.runtime.run(spec=built.spec, context=built.context, **kwargs)


@dataclass(frozen=True, slots=True)
class DelegationWorkerComponents:
    service: DelegationService
    dispatcher: Dispatcher
    assembler: PersistedChildAssembler
    executor: ChildRuntimeExecutor
    pool: "WorkerPool"


def compose_delegation_worker(
    *, store, registry, context_builder, runtime, authority_factory,
    dispatcher_config, worker_config, reference_factory=None, artifact_store=None,
    artifact_retention: timedelta | None = None, trace_bridge=None,
) -> DelegationWorkerComponents:
    """Production composition using one Store, Registry, Builder and Runtime authority path."""
    if artifact_store is None:
        raise ValueError("artifact_store_required")
    if artifact_retention is None or artifact_retention <= timedelta(0):
        raise ValueError("artifact_retention_required")
    dispatcher = Dispatcher(store, config=dispatcher_config)
    service = DelegationService(
        store=store, registry=registry,
        queue_hard_capacity=dispatcher_config.queue_hard_capacity,
    )
    assembler = PersistedChildAssembler(
        registry=registry, context_builder=context_builder,
        authority_factory=authority_factory, reference_factory=reference_factory,
    )
    from starter_agent.delegation.results import ResultAcceptanceService, ResultValidator
    executor = ChildRuntimeExecutor(
        assemble=assembler,
        runtime=runtime,
        web_researcher=None,
        cancellation_probe=lambda claim: store.parent_cancellation_version(claim.run.parent_run_id),
        checkpoint_store=store,
        artifact_store=artifact_store,
        run_store=store,
        acceptance_service=ResultAcceptanceService(store=store, validator=ResultValidator(registry), artifact_store=artifact_store),
        artifact_retention=artifact_retention,
    )
    pool = WorkerPool(
        dispatcher=dispatcher, execute=executor, config=worker_config,
        trace_bridge=trace_bridge,
    )
    return DelegationWorkerComponents(service, dispatcher, assembler, executor, pool)


@dataclass(frozen=True, slots=True)
class WorkerPoolConfig:
    global_concurrency: int = 4
    specialist_concurrency: dict[str, int] = field(default_factory=dict)
    poll_interval_seconds: float = 0.25
    heartbeat_interval_seconds: float = 10.0

    def __post_init__(self) -> None:
        if self.global_concurrency < 1 or self.poll_interval_seconds <= 0 or self.heartbeat_interval_seconds <= 0:
            raise ValueError("worker pool limits must be positive")
        if any(value < 1 for value in self.specialist_concurrency.values()):
            raise ValueError("specialist concurrency must be positive")


class WorkerPool:
    """Bounded execution wrapper; database transactions end before execute is called."""

    def __init__(self, *, dispatcher: Dispatcher, execute: Callable[[object], Awaitable[object]], config: WorkerPoolConfig, trace_bridge=None) -> None:
        self.dispatcher = dispatcher
        self.execute = execute
        self.config = config
        self.trace_bridge = trace_bridge
        self._global = asyncio.Semaphore(config.global_concurrency)
        self._specialist_active = {name: 0 for name in config.specialist_concurrency}
        self._claim_lock = asyncio.Lock()
        self._stopping = asyncio.Event()
        self._execution_tasks: set[asyncio.Task] = set()

    def stop(self) -> None:
        self._stopping.set()
        for task in tuple(self._execution_tasks):
            task.cancel()

    async def run_once(self, worker_id: str) -> bool:
        if self._stopping.is_set():
            return False
        await self._global.acquire()
        if self._stopping.is_set():
            self._global.release()
            return False
        claim = None
        specialist_name = None
        try:
            async with self._claim_lock:
                if self._stopping.is_set():
                    return False
                eligible = frozenset(
                    name for name, limit in self.config.specialist_concurrency.items()
                    if self._specialist_active[name] < limit
                )
                kwargs = {"worker_id": worker_id}
                if self.config.specialist_concurrency:
                    kwargs["excluded_specialists"] = frozenset(self.config.specialist_concurrency) - eligible
                claim = self.dispatcher.claim_next(**kwargs)
                if claim is not None and claim.specialist_id in self._specialist_active:
                    specialist_name = claim.specialist_id
                    self._specialist_active[specialist_name] += 1
            if claim is None:
                return False
        finally:
            if claim is None:
                self._global.release()
        assert claim is not None
        heartbeat_stop = asyncio.Event()

        async def heartbeat_loop() -> None:
            nonlocal claim
            while not heartbeat_stop.is_set():
                try:
                    await asyncio.wait_for(
                        heartbeat_stop.wait(),
                        timeout=self.config.heartbeat_interval_seconds,
                    )
                except TimeoutError:
                    claim = self.dispatcher.heartbeat(claim)

        heartbeat_task = asyncio.create_task(heartbeat_loop())
        outcome = None
        try:
            async def bounded_execute():
                return await self.execute(claim)

            execution_task = asyncio.create_task(bounded_execute())
            self._execution_tasks.add(execution_task)
            timeout = max(0.0, (claim.run.deadline_at - datetime.now(UTC)).total_seconds()) if hasattr(claim.run, "deadline_at") else None
            done, _ = await asyncio.wait(
                {execution_task, heartbeat_task}, timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED
            )
            if not done:
                execution_task.cancel()
                await asyncio.gather(execution_task, return_exceptions=True)
                self.dispatcher.timeout(claim)
                return True
            if heartbeat_task in done:
                error = heartbeat_task.exception()
                if error is not None:
                    execution_task.cancel()
                    await asyncio.gather(execution_task, return_exceptions=True)
                    return True
            try:
                outcome = await execution_task
            except asyncio.CancelledError:
                if self._stopping.is_set():
                    self.dispatcher.interrupt(claim, error_code="worker_interrupted")
                    return True
                raise
            completed = self.dispatcher.finish(claim, outcome)
            accept = getattr(self.execute, "accept_completed", None)
            if callable(accept) and getattr(completed, "status", None) in {"succeeded", "partial"}:
                accept(outcome, now=datetime.now(UTC))
        except TimeoutError:
            self.dispatcher.retry(claim, error_code="worker_execution_timeout")
        except asyncio.CancelledError:
            if 'execution_task' in locals():
                execution_task.cancel()
                await asyncio.gather(execution_task, return_exceptions=True)
            self.dispatcher.interrupt(claim, error_code="worker_interrupted")
            raise
        except Exception as exc:
            from starter_agent.observability.logging import get_logger

            get_logger(
                parent_run_id=claim.parent.id,
                child_run_id=claim.run.id,
            ).error(
                "delegation_worker_execution_failed",
                error_code=getattr(exc, "code", "worker_execution_error"),
                error_type=type(exc).__name__,
            )
            self.dispatcher.retry(claim, error_code="worker_execution_error")
        finally:
            if claim is not None and self.trace_bridge is not None:
                # The bridge reads the same durable event sequence; a failed
                # observability write must not alter Worker state transitions.
                try:
                    self.trace_bridge.sync_parent(claim.parent.id)
                except Exception:
                    pass
            if 'execution_task' in locals():
                self._execution_tasks.discard(execution_task)
            heartbeat_stop.set()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
            if specialist_name is not None:
                async with self._claim_lock:
                    self._specialist_active[specialist_name] -= 1
            self._global.release()
        return True

    async def serve(self, worker_id: str) -> None:
        while not self._stopping.is_set():
            if await self.run_once(worker_id):
                continue
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self.config.poll_interval_seconds)
            except TimeoutError:
                pass
