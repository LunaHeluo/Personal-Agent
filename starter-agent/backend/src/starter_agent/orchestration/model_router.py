from __future__ import annotations

from datetime import datetime
from typing import Literal, Mapping

from starter_agent.orchestration.models import (
    BudgetAmounts,
    ModelCandidate,
    ModelDecision,
    ModelRequirements,
)
from starter_agent.settings import AgentSettings, ModelRouteProfile


ModelPurpose = Literal["router", "planner", "executor", "judge", "recovery"]
ModelHealth = Literal["healthy", "degraded", "unavailable", "unknown"]


class ModelRouter:
    """Select a configured model; never instantiates a provider or calls one."""

    def __init__(self, settings: AgentSettings) -> None:
        self.settings = settings

    def decide(
        self,
        *,
        decision_id: str,
        purpose: ModelPurpose,
        requirements: ModelRequirements,
        remaining_budget: BudgetAmounts | None,
        health: Mapping[str, ModelHealth],
        created_at: datetime,
        parent_run_id: str | None = None,
        step_id: str | None = None,
        budget_snapshot_id: str | None = None,
    ) -> ModelDecision:
        eligible = [
            profile
            for profile in self._profiles()
            if self._eligible(
                profile,
                purpose=purpose,
                requirements=requirements,
                remaining_budget=remaining_budget,
            )
        ]
        eligible.sort(key=lambda item: (item.priority, item.provider, item.model))
        candidates = tuple(
            self._candidate(profile, health=health) for profile in eligible
        )
        fallback_chain = tuple(
            f"{profile.provider}/{profile.model}" for profile in eligible
        )
        selectable = [
            (index, profile)
            for index, profile in enumerate(eligible)
            if health.get(f"{profile.provider}/{profile.model}", "unknown")
            != "unavailable"
        ]
        health_order = {"healthy": 0, "unknown": 1, "degraded": 2}
        selectable.sort(
            key=lambda item: (
                health_order[
                    health.get(
                        f"{item[1].provider}/{item[1].model}", "unknown"
                    )
                ],
                item[1].priority,
                item[1].provider,
                item[1].model,
            )
        )
        if not selectable:
            return ModelDecision(
                model_decision_id=decision_id,
                parent_run_id=parent_run_id,
                step_id=step_id,
                purpose=purpose,
                requirements=requirements,
                candidates=candidates,
                reason_code="no_eligible_model",
                reason_summary=(
                    "No enabled configured model satisfies capability, context, "
                    "latency, health and budget requirements."
                ),
                fallback_chain=fallback_chain,
                config_revision=self.settings.model_routing.config_revision,
                budget_snapshot_id=budget_snapshot_id,
                status="unavailable",
                created_at=created_at,
            )
        original_index, selected = selectable[0]
        selected_health = health.get(
            f"{selected.provider}/{selected.model}", "unknown"
        )
        status = "selected" if original_index == 0 else "fallback"
        reason_code = (
            "best_eligible_candidate"
            if status == "selected"
            else "configured_fallback_selected"
        )
        pricing = self.settings.providers[selected.provider].pricing.get(selected.model)
        return ModelDecision(
            model_decision_id=decision_id,
            parent_run_id=parent_run_id,
            step_id=step_id,
            purpose=purpose,
            requirements=requirements,
            candidates=candidates,
            selected_provider=selected.provider,
            selected_model=selected.model,
            reason_code=reason_code,
            reason_summary=(
                f"Selected configured candidate with priority {selected.priority}, "
                f"health {selected_health}, latency {selected.latency_class}; risk "
                "policy remains enforced by verifier and approval edges."
            ),
            fallback_chain=fallback_chain,
            config_revision=self.settings.model_routing.config_revision,
            pricing_version=None if pricing is None else pricing.price_version,
            budget_snapshot_id=budget_snapshot_id,
            status=status,
            created_at=created_at,
        )

    def _profiles(self) -> tuple[ModelRouteProfile, ...]:
        if self.settings.model_routing.profiles:
            return tuple(self.settings.model_routing.profiles)
        # A conservative derived profile keeps legacy configuration usable for
        # requests with no special capability requirement.  Model names still
        # come exclusively from current settings.
        profiles: list[ModelRouteProfile] = []
        for provider_name, provider in self.settings.providers.items():
            model_names = list(provider.models)
            if (
                not model_names
                and provider_name == self.settings.model.default_provider
            ):
                model_names = [self.settings.model.default_model]
            for model_name in model_names:
                profiles.append(
                    ModelRouteProfile(
                        provider=provider_name,
                        model=model_name,
                        purposes=["router", "planner", "executor", "judge", "recovery"],
                        capabilities=[],
                        complexities=["trivial", "bounded", "complex"],
                        latency_class="standard",
                        max_context_tokens=self.settings.context.max_total_tokens,
                        priority=(
                            0
                            if provider_name == self.settings.model.default_provider
                            and model_name == self.settings.model.default_model
                            else 100
                        ),
                    )
                )
        return tuple(profiles)

    def _eligible(
        self,
        profile: ModelRouteProfile,
        *,
        purpose: ModelPurpose,
        requirements: ModelRequirements,
        remaining_budget: BudgetAmounts | None,
    ) -> bool:
        if not profile.enabled or purpose not in profile.purposes:
            return False
        provider = self.settings.providers.get(profile.provider)
        if provider is None:
            return False
        configured_models = set(provider.models)
        if configured_models:
            if profile.model not in configured_models:
                return False
        elif not (
            profile.provider == self.settings.model.default_provider
            and profile.model == self.settings.model.default_model
        ):
            return False
        if not set(requirements.capabilities).issubset(profile.capabilities):
            return False
        if requirements.complexity not in profile.complexities:
            return False
        if requirements.context_tokens > profile.max_context_tokens:
            return False
        if not _latency_compatible(
            candidate=profile.latency_class,
            requested=requirements.latency_class,
        ):
            return False
        if remaining_budget is not None:
            estimate = profile.estimated_cost_microunits
            if estimate is None or estimate > remaining_budget.cost_microunits:
                return False
        return True

    @staticmethod
    def _candidate(
        profile: ModelRouteProfile,
        *,
        health: Mapping[str, ModelHealth],
    ) -> ModelCandidate:
        return ModelCandidate(
            provider=profile.provider,
            model=profile.model,
            capabilities=tuple(profile.capabilities),
            cost_estimate_microunits=profile.estimated_cost_microunits,
            latency_class=profile.latency_class,
            health=health.get(f"{profile.provider}/{profile.model}", "unknown"),
        )


def _latency_compatible(*, candidate: str, requested: str) -> bool:
    maximum = {"interactive": 0, "standard": 1, "background": 2}[requested]
    observed = {"interactive": 0, "standard": 1, "background": 2}[candidate]
    return observed <= maximum

