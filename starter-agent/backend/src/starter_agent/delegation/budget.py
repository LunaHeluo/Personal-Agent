from __future__ import annotations

from dataclasses import dataclass

from starter_agent.delegation.models import (
    BudgetAllocation,
    BudgetDimension,
    BudgetLimits,
    BudgetUsage,
)


_DIMENSIONS = tuple(BudgetLimits.model_fields)


class BudgetLedgerError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        dimension: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.dimension = dimension


@dataclass(frozen=True, slots=True)
class BudgetReservation:
    updated_reserved: BudgetLimits
    allocations: tuple[BudgetAllocation, ...]


@dataclass(frozen=True, slots=True)
class BudgetSettlement:
    updated_reserved: BudgetLimits
    updated_consumed: BudgetLimits
    allocations: tuple[BudgetAllocation, ...]


def reserve_budget(
    *,
    total: BudgetLimits,
    reserved: BudgetLimits,
    consumed: BudgetLimits,
    requested: BudgetLimits,
) -> BudgetReservation:
    updated: dict[str, int] = {}
    allocations: list[BudgetAllocation] = []
    for dimension in _DIMENSIONS:
        total_value = getattr(total, dimension)
        reserved_value = getattr(reserved, dimension)
        consumed_value = getattr(consumed, dimension)
        requested_value = getattr(requested, dimension)
        if consumed_value > reserved_value or reserved_value > total_value:
            raise BudgetLedgerError(
                "invalid_parent_budget_state",
                f"invalid parent budget state for {dimension}",
                dimension=dimension,
            )
        if requested_value > total_value - reserved_value:
            raise BudgetLedgerError(
                "parent_budget_exhausted",
                f"parent has insufficient {dimension} budget",
                dimension=dimension,
            )
        updated[dimension] = reserved_value + requested_value
        # A legacy five-dimensional payload has an implicit zero step limit.
        # Do not materialize a sixth allocation row for that payload; new
        # orchestration runs provide a non-zero step limit/request and receive
        # the normal persisted allocation.
        if dimension != "steps" or any(
            (total_value, reserved_value, consumed_value, requested_value)
        ):
            allocations.append(
                BudgetAllocation(
                    dimension=dimension,
                    limit=total_value,
                    requested=requested_value,
                    reserved=requested_value,
                    consumed=0,
                    released=0,
                )
            )
    return BudgetReservation(
        updated_reserved=BudgetLimits(**updated),
        allocations=tuple(allocations),
    )


def validate_cost_usage(usage: BudgetUsage) -> None:
    if usage.cost_status == "unknown" or usage.usage_source is None:
        raise BudgetLedgerError(
            "cost_budget_unenforceable",
            "cost usage requires an auditable usage source",
            dimension="cost_microunits",
        )
    if usage.cost_status == "estimated" and usage.price_version is None:
        raise BudgetLedgerError(
            "cost_budget_unenforceable",
            "estimated cost usage requires a price version",
            dimension="cost_microunits",
        )


def settle_budget(
    *,
    parent_reserved: BudgetLimits,
    parent_consumed: BudgetLimits,
    allocations: tuple[BudgetAllocation, ...],
    usage: BudgetUsage,
    release_unused: bool = True,
) -> BudgetSettlement:
    validate_cost_usage(usage)
    by_dimension = {item.dimension: item for item in allocations}
    if len(by_dimension) != len(allocations):
        raise BudgetLedgerError(
            "duplicate_budget_allocation",
            "budget allocations must contain each dimension at most once",
        )

    updated_reserved: dict[str, int] = {}
    updated_consumed: dict[str, int] = {}
    settled: list[BudgetAllocation] = []
    for dimension in _DIMENSIONS:
        allocation = by_dimension.get(dimension)
        actual = getattr(usage, dimension)
        if allocation is None:
            if actual:
                raise BudgetLedgerError(
                    "budget_usage_exceeds_reservation",
                    f"usage has no {dimension} reservation",
                    dimension=dimension,
                )
            updated_reserved[dimension] = getattr(parent_reserved, dimension)
            updated_consumed[dimension] = getattr(parent_consumed, dimension)
            continue
        if allocation.released:
            raise BudgetLedgerError(
                "budget_already_settled",
                f"{dimension} allocation was already settled",
                dimension=dimension,
            )
        if actual < allocation.consumed or actual > allocation.reserved:
            raise BudgetLedgerError(
                "budget_usage_exceeds_reservation",
                f"{dimension} usage exceeds reservation",
                dimension=dimension,
            )
        released = allocation.reserved - actual if release_unused else 0
        parent_reserved_value = getattr(parent_reserved, dimension)
        if released > parent_reserved_value:
            raise BudgetLedgerError(
                "invalid_parent_budget_state",
                f"parent {dimension} reservation is smaller than release",
                dimension=dimension,
            )
        updated_reserved[dimension] = parent_reserved_value - released
        updated_consumed[dimension] = getattr(parent_consumed, dimension) + (actual - allocation.consumed)
        settled.append(
            BudgetAllocation(
                **{
                    **allocation.model_dump(mode="python"),
                    "consumed": actual,
                    "released": released,
                    "estimated": usage.estimated,
                    "price_version": usage.price_version,
                    "usage_source": usage.usage_source,
                    "version": allocation.version + 1,
                }
            )
        )
    return BudgetSettlement(
        updated_reserved=BudgetLimits(**updated_reserved),
        updated_consumed=BudgetLimits(**updated_consumed),
        allocations=tuple(settled),
    )
