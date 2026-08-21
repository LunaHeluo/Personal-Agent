from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping

from starter_agent.delegation.models import BudgetLimits
from starter_agent.orchestration.models import (
    BudgetAmounts,
    BudgetSnapshot,
    Identifier,
)


_DIMENSIONS = tuple(BudgetAmounts.model_fields)


class BudgetPreflightDenied(ValueError):
    def __init__(self, code: str, *, dimension: str | None = None) -> None:
        super().__init__(f"{code}: {dimension or 'budget'}")
        self.code = code
        self.dimension = dimension


@dataclass(frozen=True, slots=True)
class BudgetPermit:
    allowed: bool
    reason_code: str
    stop_dimension: str | None = None


@dataclass(frozen=True, slots=True)
class FanoutReservation:
    snapshot: BudgetSnapshot
    child_requests: Mapping[str, BudgetAmounts]


@dataclass(frozen=True, slots=True)
class BudgetStopResult:
    reason_code: str
    stop_dimension: str | None
    completed: tuple[str, ...]
    incomplete: tuple[str, ...]
    snapshot: BudgetSnapshot
    recovery_actions: tuple[str, ...]


def from_delegation_budget(value: BudgetLimits) -> BudgetAmounts:
    """Adapt the existing ledger model without creating another authority."""

    return BudgetAmounts(**value.model_dump(mode="python"))


def to_delegation_budget(value: BudgetAmounts) -> BudgetLimits:
    return BudgetLimits(**value.model_dump(mode="python"))


class OrchestrationBudgetManager:
    """Pure orchestration facade over the existing budget dimensions.

    Persistence and CAS remain the responsibility of ``SQLiteRunStore``.  The
    applied operation IDs travel with the persisted snapshot, making retries
    deterministic without an in-memory or second budget ledger.
    """

    def initial_snapshot(
        self,
        *,
        snapshot_id: str,
        parent_run_id: str,
        limit: BudgetAmounts,
        created_at: datetime,
        child_run_id: str | None = None,
        step_id: str | None = None,
        cost_status: str = "actual",
        price_version: str | None = None,
        usage_source: str | None = None,
    ) -> BudgetSnapshot:
        zero = _zero()
        return BudgetSnapshot(
            budget_snapshot_id=snapshot_id,
            parent_run_id=parent_run_id,
            child_run_id=child_run_id,
            step_id=step_id,
            phase="preflight",
            limit=limit,
            reserved=zero,
            consumed=zero,
            released=zero,
            remaining=limit,
            overage=zero,
            cost_status=cost_status,
            price_version=price_version,
            usage_source=usage_source,
            created_at=created_at,
        )

    def preflight(
        self,
        snapshot: BudgetSnapshot,
        requested: BudgetAmounts,
    ) -> BudgetPermit:
        if snapshot.phase == "stopped":
            return BudgetPermit(False, "budget_stopped", snapshot.stop_dimension)
        for dimension in _DIMENSIONS:
            if getattr(requested, dimension) > getattr(snapshot.remaining, dimension):
                return BudgetPermit(False, "budget_insufficient", dimension)
        return BudgetPermit(True, "budget_available")

    def reserve_fanout(
        self,
        snapshot: BudgetSnapshot,
        *,
        operation_id: str,
        child_requests: Mapping[str, BudgetAmounts],
        snapshot_id: str,
        created_at: datetime,
    ) -> FanoutReservation:
        if operation_id in snapshot.applied_operation_ids:
            return FanoutReservation(snapshot=snapshot, child_requests=dict(child_requests))
        requested = _sum(child_requests.values())
        permit = self.preflight(snapshot, requested)
        if not permit.allowed:
            raise BudgetPreflightDenied(permit.reason_code, dimension=permit.stop_dimension)
        updated_reserved = _add(snapshot.reserved, requested)
        updated_remaining = _subtract(snapshot.remaining, requested)
        updated = snapshot.model_copy(
            update={
                "budget_snapshot_id": snapshot_id,
                "version": snapshot.version + 1,
                "phase": "reserved",
                "reserved": updated_reserved,
                "remaining": updated_remaining,
                "applied_operation_ids": _append_operation(snapshot, operation_id),
                "created_at": created_at,
            }
        )
        return FanoutReservation(snapshot=updated, child_requests=dict(child_requests))

    def consume(
        self,
        snapshot: BudgetSnapshot,
        *,
        operation_id: str,
        usage: BudgetAmounts,
        snapshot_id: str,
        created_at: datetime,
    ) -> BudgetSnapshot:
        if operation_id in snapshot.applied_operation_ids:
            return snapshot
        consumed: dict[str, int] = {}
        remaining: dict[str, int] = {}
        overage = snapshot.overage.model_dump(mode="python")
        stop_dimension: str | None = None
        for dimension in _DIMENSIONS:
            observed = getattr(snapshot.consumed, dimension) + getattr(usage, dimension)
            limit = getattr(snapshot.limit, dimension)
            reserved = getattr(snapshot.reserved, dimension)
            if observed + reserved > limit:
                overage[dimension] += observed + reserved - limit
                stop_dimension = stop_dimension or dimension
            consumed[dimension] = min(observed, limit)
            remaining[dimension] = max(limit - consumed[dimension] - reserved, 0)
        return snapshot.model_copy(
            update={
                "budget_snapshot_id": snapshot_id,
                "version": snapshot.version + 1,
                "phase": "stopped" if stop_dimension else "consumed",
                "consumed": BudgetAmounts(**consumed),
                "remaining": BudgetAmounts(**remaining),
                "overage": BudgetAmounts(**overage),
                "stop_dimension": stop_dimension,
                "applied_operation_ids": _append_operation(snapshot, operation_id),
                "created_at": created_at,
            }
        )

    def settle_reservation(
        self,
        snapshot: BudgetSnapshot,
        *,
        operation_id: str,
        reserved_amount: BudgetAmounts,
        usage: BudgetAmounts,
        snapshot_id: str,
        created_at: datetime,
    ) -> BudgetSnapshot:
        if operation_id in snapshot.applied_operation_ids:
            return snapshot
        reserved: dict[str, int] = {}
        consumed: dict[str, int] = {}
        released: dict[str, int] = {}
        remaining: dict[str, int] = {}
        overage = snapshot.overage.model_dump(mode="python")
        stop_dimension: str | None = None
        for dimension in _DIMENSIONS:
            reservation = getattr(reserved_amount, dimension)
            actual = getattr(usage, dimension)
            current_reserved = getattr(snapshot.reserved, dimension)
            if reservation > current_reserved:
                raise BudgetPreflightDenied(
                    "reservation_not_found", dimension=dimension
                )
            if actual > reservation:
                raise BudgetPreflightDenied(
                    "usage_exceeds_reservation", dimension=dimension
                )
            reserved[dimension] = current_reserved - reservation
            consumed[dimension] = getattr(snapshot.consumed, dimension) + actual
            released[dimension] = (
                getattr(snapshot.released, dimension) + reservation - actual
            )
            limit = getattr(snapshot.limit, dimension)
            if consumed[dimension] + reserved[dimension] > limit:
                overage[dimension] += consumed[dimension] + reserved[dimension] - limit
                consumed[dimension] = min(consumed[dimension], limit)
                stop_dimension = stop_dimension or dimension
            remaining[dimension] = max(
                limit - consumed[dimension] - reserved[dimension], 0
            )
        return snapshot.model_copy(
            update={
                "budget_snapshot_id": snapshot_id,
                "version": snapshot.version + 1,
                "phase": "stopped" if stop_dimension else "settled",
                "reserved": BudgetAmounts(**reserved),
                "consumed": BudgetAmounts(**consumed),
                "released": BudgetAmounts(**released),
                "remaining": BudgetAmounts(**remaining),
                "overage": BudgetAmounts(**overage),
                "stop_dimension": stop_dimension,
                "applied_operation_ids": _append_operation(snapshot, operation_id),
                "created_at": created_at,
            }
        )

    @staticmethod
    def stop_result(
        snapshot: BudgetSnapshot,
        *,
        completed: tuple[str, ...],
        incomplete: tuple[str, ...],
    ) -> BudgetStopResult:
        return BudgetStopResult(
            reason_code="budget_exhausted",
            stop_dimension=snapshot.stop_dimension,
            completed=completed,
            incomplete=incomplete,
            snapshot=snapshot,
            recovery_actions=("increase_budget", "start_new_run"),
        )


def _zero() -> BudgetAmounts:
    return BudgetAmounts()


def _sum(values: Iterable[BudgetAmounts]) -> BudgetAmounts:
    totals = {dimension: 0 for dimension in _DIMENSIONS}
    for value in values:
        for dimension in _DIMENSIONS:
            totals[dimension] += getattr(value, dimension)
    return BudgetAmounts(**totals)


def _add(left: BudgetAmounts, right: BudgetAmounts) -> BudgetAmounts:
    return BudgetAmounts(
        **{
            dimension: getattr(left, dimension) + getattr(right, dimension)
            for dimension in _DIMENSIONS
        }
    )


def _subtract(left: BudgetAmounts, right: BudgetAmounts) -> BudgetAmounts:
    values: dict[str, int] = {}
    for dimension in _DIMENSIONS:
        result = getattr(left, dimension) - getattr(right, dimension)
        if result < 0:
            raise BudgetPreflightDenied("budget_insufficient", dimension=dimension)
        values[dimension] = result
    return BudgetAmounts(**values)


def _append_operation(snapshot: BudgetSnapshot, operation_id: str) -> tuple[str, ...]:
    # The snapshot field is bounded.  A persistent Run Event remains the full
    # audit log; retaining recent operation IDs is enough for retry idempotency.
    return (*snapshot.applied_operation_ids[-4095:], operation_id)

