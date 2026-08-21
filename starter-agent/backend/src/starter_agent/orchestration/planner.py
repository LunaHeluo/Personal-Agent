from __future__ import annotations

import json
import re
from datetime import datetime

from pydantic import Field, ValidationError

from starter_agent.domain.models import Message
from starter_agent.orchestration.models import (
    BudgetAmounts,
    ModelDecision,
    OrchestrationModel,
    Plan,
    RouteDecision,
)
from starter_agent.providers.base import Provider


class PlannerRequest(OrchestrationModel):
    parent_run_id: str = Field(min_length=1, max_length=160)
    goal: str = Field(min_length=1, max_length=4_000)
    confirmed_facts: tuple[str, ...] = Field(default_factory=tuple, max_length=128)
    input_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=256)
    capability_summary: tuple[str, ...] = Field(default_factory=tuple, max_length=128)
    risk_boundaries: tuple[str, ...] = Field(default_factory=tuple, max_length=128)
    budget_total: BudgetAmounts
    deadline_at: datetime
    background_allowed: bool = True


class PlannerOutputError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Planner:
    """Structured planning policy for the complex route only."""

    async def create_plan(
        self,
        request: PlannerRequest,
        *,
        route_decision: RouteDecision,
        model_decision: ModelDecision,
        provider: Provider,
    ) -> Plan:
        if route_decision.route != "plan_delegation":
            raise ValueError("planner_route_not_allowed")
        if model_decision.purpose != "planner" or model_decision.status == "unavailable":
            raise PlannerOutputError("planner_model_unavailable")
        if (
            model_decision.selected_provider is None
            or model_decision.selected_model is None
        ):
            raise PlannerOutputError("planner_model_unavailable")
        if provider.name != model_decision.selected_provider:
            raise PlannerOutputError("planner_provider_binding_mismatch")

        last_error = "planner_output_invalid"
        for attempt in range(2):
            response = await provider.complete(
                self._messages(request, retry=attempt > 0),
                model_decision.selected_model,
                tools=[],
            )
            try:
                plan = Plan.model_validate_json(_normalize_json(response.content or ""))
            except (ValidationError, ValueError):
                last_error = "planner_output_schema_invalid"
                continue
            if plan.parent_run_id != request.parent_run_id:
                last_error = "planner_parent_binding_mismatch"
                continue
            if plan.goal != request.goal:
                last_error = "planner_goal_binding_mismatch"
                continue
            return plan
        raise PlannerOutputError(last_error)

    @staticmethod
    def _messages(request: PlannerRequest, *, retry: bool) -> list[Message]:
        payload = {
            "parent_run_id": request.parent_run_id,
            "goal": request.goal,
            "confirmed_facts": request.confirmed_facts,
            "input_refs": request.input_refs,
            "capabilities": request.capability_summary,
            "risk_boundaries": request.risk_boundaries,
            "budget_total": request.budget_total.model_dump(mode="json"),
            "deadline_at": request.deadline_at.isoformat(),
            "background_allowed": request.background_allowed,
        }
        correction = (
            "The previous response failed the Plan schema. Return one corrected JSON object only."
            if retry
            else ""
        )
        return [
            Message(
                role="system",
                content=(
                    "Create a dependency DAG for the supplied complex task. Return only a JSON "
                    "object matching the Plan schema. Every step must contain goal, input_refs, "
                    "capabilities, done_when, risk, budget_limit, deadline_at, depends_on, "
                    "execution and output_contract_ref. Do not call tools and do not perform any "
                    f"step. {correction}"
                ),
            ),
            Message(role="user", content=json.dumps(payload, ensure_ascii=False)),
        ]


_FENCED_JSON = re.compile(
    r"\A```(?:json)?[ \t]*\r?\n(?P<body>.*)\r?\n```[ \t]*\Z",
    re.IGNORECASE | re.DOTALL,
)


def _normalize_json(content: str) -> str:
    stripped = content.strip()
    match = _FENCED_JSON.fullmatch(stripped)
    return match.group("body").strip() if match else stripped

