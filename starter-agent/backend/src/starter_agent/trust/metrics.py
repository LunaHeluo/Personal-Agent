from __future__ import annotations

from statistics import median

from starter_agent.trust.models import EvalAssertionResult, EvalCaseResult, EvalMetric


class ProgrammaticMetricCalculator:
    def calculate(
        self,
        *,
        run_id: str,
        case_results: list[EvalCaseResult],
        assertion_results: list[EvalAssertionResult],
    ) -> list[EvalMetric]:
        return [
            self._task_success(run_id, case_results),
            self._outcome_average(
                run_id,
                case_results,
                name="Source Completeness",
                field="source_completeness",
                unit="ratio",
            ),
            self._outcome_average(
                run_id,
                case_results,
                name="Evidence Fidelity",
                field="evidence_fidelity",
                unit="ratio",
            ),
            self._outcome_average(
                run_id,
                case_results,
                name="Failure Complexity",
                field="failure_complexity",
                unit="score",
            ),
            self._assertion_metric(
                run_id,
                assertion_results,
                name="Tool / Argument Accuracy",
                prefixes=("tool_call", "schema", "argument"),
            ),
            self._assertion_metric(
                run_id,
                assertion_results,
                name="Citation Correctness",
                prefixes=("citation", "source"),
            ),
            self._assertion_metric(
                run_id,
                assertion_results,
                name="Approval Compliance",
                prefixes=("approval", "policy"),
            ),
            self._latency(run_id, case_results, name="Latency P50", percentile=50),
            self._latency(run_id, case_results, name="Latency P95", percentile=95),
            self._total_tokens(run_id, case_results),
            self._cost_per_success(run_id, case_results),
        ]

    def _outcome_average(
        self,
        run_id: str,
        case_results: list[EvalCaseResult],
        *,
        name: str,
        field: str,
        unit: str,
    ) -> EvalMetric:
        values = [
            float(value)
            for result in case_results
            if isinstance((value := result.outcome_summary.get(field)), (int, float))
            and not isinstance(value, bool)
        ]
        total = sum(values)
        return EvalMetric(
            id=self._metric_id(run_id, name),
            run_id=run_id,
            name=name,
            value=None if not values else total / len(values),
            numerator=None if not values else total,
            denominator=len(values),
            unit=unit,
            missing=not values,
        )

    def _task_success(
        self,
        run_id: str,
        case_results: list[EvalCaseResult],
    ) -> EvalMetric:
        denominator = len([item for item in case_results if item.status != "skipped"])
        numerator = len([item for item in case_results if item.status == "passed"])
        return self._ratio(
            run_id,
            name="Task Success",
            numerator=numerator,
            denominator=denominator,
        )

    def _assertion_metric(
        self,
        run_id: str,
        assertion_results: list[EvalAssertionResult],
        *,
        name: str,
        prefixes: tuple[str, ...],
    ) -> EvalMetric:
        selected = [
            item
            for item in assertion_results
            if item.assertion_id.removeprefix("rule:").startswith(prefixes)
        ]
        numerator = len([item for item in selected if item.status == "passed"])
        return self._ratio(
            run_id,
            name=name,
            numerator=numerator,
            denominator=len(selected),
        )

    def _latency(
        self,
        run_id: str,
        case_results: list[EvalCaseResult],
        *,
        name: str,
        percentile: int,
    ) -> EvalMetric:
        values = sorted(
            float(item.outcome_summary["duration_ms"])
            for item in case_results
            if "duration_ms" in item.outcome_summary
        )
        if not values:
            return EvalMetric(
                id=self._metric_id(run_id, name),
                run_id=run_id,
                name=name,
                value=None,
                unit="ms",
                missing=True,
            )
        if percentile == 50:
            value = float(median(values))
        else:
            rank = (len(values) - 1) * (percentile / 100)
            lower = int(rank)
            upper = min(lower + 1, len(values) - 1)
            fraction = rank - lower
            value = values[lower] + (values[upper] - values[lower]) * fraction
        return EvalMetric(
            id=self._metric_id(run_id, name),
            run_id=run_id,
            name=name,
            value=value,
            numerator=None,
            denominator=len(values),
            unit="ms",
        )

    def _total_tokens(
        self,
        run_id: str,
        case_results: list[EvalCaseResult],
    ) -> EvalMetric:
        values = []
        for result in case_results:
            usage = result.outcome_summary.get("token_usage")
            if isinstance(usage, dict) and usage.get("total_tokens") is not None:
                values.append(float(usage["total_tokens"]))
        return EvalMetric(
            id=self._metric_id(run_id, "Total Tokens"),
            run_id=run_id,
            name="Total Tokens",
            value=sum(values) if values else None,
            numerator=sum(values) if values else None,
            denominator=len(values),
            unit="tokens",
            missing=not values,
        )

    def _cost_per_success(
        self,
        run_id: str,
        case_results: list[EvalCaseResult],
    ) -> EvalMetric:
        costs = [
            float(result.outcome_summary["cost_usd"])
            for result in case_results
            if "cost_usd" in result.outcome_summary
        ]
        successes = len([item for item in case_results if item.status == "passed"])
        if not costs or successes == 0:
            return EvalMetric(
                id=self._metric_id(run_id, "Cost per Successful Task"),
                run_id=run_id,
                name="Cost per Successful Task",
                value=None,
                numerator=sum(costs) if costs else None,
                denominator=successes,
                unit="usd/success",
                missing=True,
            )
        total_cost = sum(costs)
        return EvalMetric(
            id=self._metric_id(run_id, "Cost per Successful Task"),
            run_id=run_id,
            name="Cost per Successful Task",
            value=round(total_cost / successes, 6),
            numerator=total_cost,
            denominator=successes,
            unit="usd/success",
        )

    def _ratio(
        self,
        run_id: str,
        *,
        name: str,
        numerator: float,
        denominator: float,
    ) -> EvalMetric:
        return EvalMetric(
            id=self._metric_id(run_id, name),
            run_id=run_id,
            name=name,
            value=None if denominator == 0 else numerator / denominator,
            numerator=numerator,
            denominator=denominator,
            unit="ratio",
            missing=denominator == 0,
        )

    def _metric_id(self, run_id: str, name: str) -> str:
        slug = (
            name.casefold()
            .replace("/", "")
            .replace(" ", "-")
            .replace("(", "")
            .replace(")", "")
        )
        return f"{run_id}:{slug}"
