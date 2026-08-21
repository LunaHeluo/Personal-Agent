from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from starter_agent.capabilities.models import canonical_json_sha256
from starter_agent.delegation.models import RunOutcome
from starter_agent.delegation.store import ClaimedChild, RevisionConflictError, SQLiteRunStore

DEFAULT_QUEUE_HARD_CAPACITY = 500

class DispatcherError(RuntimeError):
    code = "dispatcher_error"


class RunQueueOverloaded(DispatcherError):
    code = "run_queue_overloaded"


@dataclass(frozen=True, slots=True)
class DispatcherConfig:
    lease_ttl: timedelta = timedelta(seconds=30)
    queue_high_watermark: int = 100
    queue_hard_capacity: int = DEFAULT_QUEUE_HARD_CAPACITY
    max_attempts: int = 3
    retry_base_delay: timedelta = timedelta(seconds=1)

    def __post_init__(self) -> None:
        if self.lease_ttl <= timedelta(0) or self.retry_base_delay < timedelta(0):
            raise ValueError("lease and retry durations must be valid")
        if not 0 <= self.queue_high_watermark <= self.queue_hard_capacity:
            raise ValueError("queue watermarks are invalid")


@dataclass(frozen=True, slots=True)
class QueuePressure:
    depth: int
    high_watermark_reached: bool
    hard_capacity_reached: bool


class Dispatcher:
    def __init__(self, store: SQLiteRunStore, *, config: DispatcherConfig, now=None) -> None:
        self.store = store
        self.config = config
        self._now = now or (lambda: datetime.now(UTC))

    def queue_pressure(self, *, now: datetime | None = None) -> QueuePressure:
        depth = self.store.queue_depth(now=now or self._now())
        return QueuePressure(depth, depth >= self.config.queue_high_watermark, depth >= self.config.queue_hard_capacity)

    def ensure_capacity(self, *, now: datetime | None = None) -> QueuePressure:
        pressure = self.queue_pressure(now=now)
        if pressure.hard_capacity_reached:
            raise RunQueueOverloaded("delegation run queue reached hard capacity")
        return pressure

    def claim_next(self, *, worker_id: str, now: datetime | None = None, excluded_specialists: frozenset[str] | None = None) -> ClaimedChild | None:
        claimed_at = now or self._now()
        expired = self.store.expire_queued_child_runs(now=claimed_at)
        for run in expired:
            self.store.wake_parent_if_children_terminal(run.parent_run_id, occurred_at=claimed_at)
        return self.store.claim_next_child_run(
            worker_id=worker_id,
            lease_token=f"lease:{uuid4().hex}",
            claimed_at=claimed_at,
            lease_ttl=self.config.lease_ttl,
            excluded_specialists=excluded_specialists,
        )

    def heartbeat(self, claim: ClaimedChild, *, now: datetime | None = None) -> ClaimedChild:
        run = self.store.heartbeat_child_run(
            claim.run.id, worker_id=claim.run.lease_owner or "", lease_token=claim.lease_token,
            expected_version=claim.run.version, heartbeat_at=now or self._now(), lease_ttl=self.config.lease_ttl,
        )
        return ClaimedChild(run, claim.task, claim.parent, claim.lease_token, claim.parent_cancellation_version)

    def cancellation_requested(self, claim: ClaimedChild) -> bool:
        version, requested = self.store.parent_cancellation_version(claim.run.parent_run_id)
        return requested or version != claim.parent_cancellation_version

    def cancel_parent(self, parent_run_id: str, *, reason: str):
        occurred_at = self._now()
        parent = self.store.request_parent_cancellation(parent_run_id, reason=reason, requested_at=occurred_at)
        terminal = self.store.wake_parent_if_children_terminal(parent_run_id, occurred_at=occurred_at)
        return terminal or parent

    def finish(self, claim: ClaimedChild, outcome: RunOutcome, *, now: datetime | None = None):
        occurred_at = now or self._now()
        if self.cancellation_requested(claim):
            completed = self.store.release_child_lease(
                claim.run.id, target_status="cancelled", worker_id=claim.run.lease_owner or "",
                lease_token=claim.lease_token, expected_version=claim.run.version,
                occurred_at=occurred_at, error_code="run_cancelled", event_type="child.cancelled",
            )
            self.store.wake_parent_if_children_terminal(claim.run.parent_run_id, occurred_at=occurred_at)
            return completed
        if outcome.status == "waiting_for_user":
            return self.store.release_child_lease(
                claim.run.id, target_status="waiting_for_user", worker_id=claim.run.lease_owner or "",
                lease_token=claim.lease_token, expected_version=claim.run.version,
                occurred_at=occurred_at, event_type="child.waiting_for_user",
                checkpoint_ref=outcome.checkpoint_ref,
            )
        if outcome.status == "cancelled":
            completed = self.store.release_child_lease(
                claim.run.id, target_status="cancelled", worker_id=claim.run.lease_owner or "",
                lease_token=claim.lease_token, expected_version=claim.run.version,
                occurred_at=occurred_at, error_code=outcome.error_code, event_type="child.cancelled",
            )
            self.store.wake_parent_if_children_terminal(claim.run.parent_run_id, occurred_at=occurred_at)
            return completed
        ref = outcome.result_envelope_ref or outcome.output_ref or f"outcome:{claim.run.id}"
        digest = outcome.result_envelope_hash or canonical_json_sha256({"run_id": claim.run.id, "status": outcome.status, "ref": ref})
        completed = self.store.complete_child_run(
            claim.run.id, target_status=outcome.status, result_envelope_ref=ref,
            result_hash=digest, worker_id=claim.run.lease_owner or "", lease_token=claim.lease_token,
            expected_version=claim.run.version, completed_at=occurred_at,
            error_code=outcome.error_code,
        )
        self.store.wake_parent_if_children_terminal(claim.run.parent_run_id, occurred_at=occurred_at)
        return completed

    def retry(self, claim: ClaimedChild, *, error_code: str, now: datetime | None = None):
        occurred_at = now or self._now()
        if claim.run.attempt >= self.config.max_attempts:
            completed = self.store.release_child_lease(
                claim.run.id, target_status="failed", worker_id=claim.run.lease_owner or "",
                lease_token=claim.lease_token, expected_version=claim.run.version,
                occurred_at=occurred_at, error_code="run_retry_exhausted", event_type="child.retry_exhausted",
            )
            self.store.wake_parent_if_children_terminal(claim.run.parent_run_id, occurred_at=occurred_at)
            return completed
        delay = self.config.retry_base_delay * (2 ** (claim.run.attempt - 1))
        return self.store.release_child_lease(
            claim.run.id, target_status="queued", worker_id=claim.run.lease_owner or "",
            lease_token=claim.lease_token, expected_version=claim.run.version,
            occurred_at=occurred_at, available_at=occurred_at + delay, increment_attempt=True,
            error_code=error_code, event_type="child.retry_scheduled",
        )

    def timeout(self, claim: ClaimedChild, *, now: datetime | None = None):
        occurred_at = now or self._now()
        completed = self.store.release_child_lease(
            claim.run.id, target_status="timed_out", worker_id=claim.run.lease_owner or "",
            lease_token=claim.lease_token, expected_version=claim.run.version,
            occurred_at=occurred_at, error_code="run_deadline_exceeded",
            event_type="child.deadline_exceeded",
        )
        self.store.wake_parent_if_children_terminal(claim.run.parent_run_id, occurred_at=occurred_at)
        return completed

    def interrupt(self, claim: ClaimedChild, *, error_code: str = "worker_interrupted", now: datetime | None = None):
        occurred_at = now or self._now()
        return self.store.release_child_lease(
            claim.run.id, target_status="queued", worker_id=claim.run.lease_owner or "",
            lease_token=claim.lease_token, expected_version=claim.run.version,
            occurred_at=occurred_at, available_at=occurred_at,
            increment_attempt=False, error_code=error_code,
            event_type="worker.interrupted",
        )

    def reap_expired(self, *, now: datetime | None = None):
        recovered_at = now or self._now()
        results = []
        for run in self.store.list_expired_child_leases(now=recovered_at):
            claim = self._claim_from_persisted(run)
            try:
                if run.attempt >= self.config.max_attempts or recovered_at >= run.deadline_at:
                    target = "timed_out" if recovered_at >= run.deadline_at else "failed"
                    completed = self.store.release_child_lease(
                        run.id, target_status=target, worker_id=run.lease_owner or "",
                        lease_token=run.lease_token or "", expected_version=run.version,
                        occurred_at=recovered_at,
                        error_code="run_deadline_exceeded" if target == "timed_out" else "run_lease_expired",
                        event_type="child.retry_exhausted",
                    )
                    results.append(completed)
                    self.store.wake_parent_if_children_terminal(run.parent_run_id, occurred_at=recovered_at)
                else:
                    results.append(self.store.release_child_lease(
                        run.id, target_status="queued", worker_id=run.lease_owner or "",
                        lease_token=run.lease_token or "", expected_version=run.version,
                        occurred_at=recovered_at, available_at=recovered_at,
                        increment_attempt=True, error_code="run_lease_expired",
                        event_type="child.lease_recovered",
                    ))
            except RevisionConflictError:
                continue
        return tuple(results)

    def _claim_from_persisted(self, run):
        tree = self.store.get_run_tree(run.parent_run_id)
        task = next(item for item in tree.child_tasks if item.id == run.child_task_id)
        return ClaimedChild(run, task, tree.parent, run.lease_token or "", tree.parent.cancellation_version)
