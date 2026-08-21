from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Protocol

from pydantic import Field

from starter_agent.capabilities.models import canonical_json_sha256
from starter_agent.delegation.models import (
    ChildRun,
    ParentRun,
    ResultEnvelope,
    TaskContract,
)
from starter_agent.delegation.store import ChildCreationResult, SQLiteRunStore
from starter_agent.orchestration.budget import to_delegation_budget
from starter_agent.orchestration.context import (
    ChildResultProjection,
    OrchestrationContextManager,
)
from starter_agent.orchestration.models import (
    Identifier,
    OrchestrationModel,
    Plan,
    PlanStep,
)


_FORBIDDEN_PACKAGE_KEYS = frozenset(
    {
        "messages",
        "full_chat",
        "conversation",
        "history",
        "scratchpad",
        "working_memory",
        "long_term_memory",
        "system_prompt",
        "tool_schema",
        "tool_schemas",
        "other_child_results",
    }
)


class ChildTaskPackage(OrchestrationModel):
    step_id: Identifier
    contract: TaskContract
    child_run: ChildRun
    artifact_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=256)
    output_contract_ref: str = Field(min_length=1, max_length=500)
    trace_context: Mapping[str, str]


class FanOutResult(OrchestrationModel):
    parent_run_id: Identifier
    packages: tuple[ChildTaskPackage, ...]


class AcceptedChildResult(OrchestrationModel):
    step_id: Identifier
    child_run_id: Identifier
    result_envelope_ref: str
    envelope: ResultEnvelope
    projection: ChildResultProjection


class ResultArtifactReader(Protocol):
    def get_tool_artifact_for_principal(
        self, artifact_ref: str, *, principal: str
    ) -> Mapping[str, Any] | None: ...


class FanOutBuilder:
    """Translate validated Plan steps into the existing Delegation contracts."""

    def build(
        self,
        *,
        parent: ParentRun,
        plan: Plan,
        selected_step_ids: tuple[str, ...],
        requested_tools: Mapping[str, tuple[str, ...]],
        policy_tools: frozenset[str],
        specialist_tools: Mapping[str, frozenset[str]],
        created_at: datetime,
        route_decision_id: str,
    ) -> FanOutResult:
        if plan.status != "valid" or plan.validation_result_id is None:
            raise ValueError("fanout_plan_not_validated")
        by_id = {step.step_id: step for step in plan.steps}
        if len(set(selected_step_ids)) != len(selected_step_ids):
            raise ValueError("fanout_step_ids_not_unique")
        packages = tuple(
            self._package(
                parent=parent,
                plan=plan,
                step=by_id[step_id],
                requested_tools=requested_tools.get(step_id, ()),
                policy_tools=policy_tools,
                specialist_tools=specialist_tools,
                created_at=created_at,
                route_decision_id=route_decision_id,
            )
            for step_id in selected_step_ids
        )
        return FanOutResult(parent_run_id=parent.id, packages=packages)

    @staticmethod
    def _package(
        *,
        parent: ParentRun,
        plan: Plan,
        step: PlanStep,
        requested_tools: tuple[str, ...],
        policy_tools: frozenset[str],
        specialist_tools: Mapping[str, frozenset[str]],
        created_at: datetime,
        route_decision_id: str,
    ) -> ChildTaskPackage:
        if step.execution != "child" or step.specialist_id is None:
            raise ValueError("fanout_requires_child_step")
        allowed = tuple(
            sorted(
                set(requested_tools)
                & policy_tools
                & specialist_tools.get(step.specialist_id, frozenset())
            )
        )
        if set(requested_tools) - set(allowed):
            raise ValueError("fanout_tool_authority_expansion")
        inputs = {
            "input_refs": list(step.input_refs),
            "artifact_refs": [
                item for item in step.input_refs if item.startswith("artifact:")
            ],
        }
        if _find_forbidden_key(inputs) is not None:
            raise ValueError("fanout_context_payload_forbidden")
        seed = {
            "parent_run_id": parent.id,
            "plan_id": plan.plan_id,
            "step_id": step.step_id,
            "plan_version": plan.version,
        }
        digest = canonical_json_sha256(seed)
        task_id = f"orchestration-task:{digest[:32]}"
        child_run_id = f"orchestration-child:{digest[:32]}:1"
        deadline = min(step.deadline_at, plan.deadline_at, parent.deadline_at)
        contract = TaskContract(
            task_id=task_id,
            parent_run_id=parent.id,
            specialist_id=step.specialist_id,
            goal=step.goal,
            inputs=inputs,
            constraints={
                "plan_id": plan.plan_id,
                "step_id": step.step_id,
                "done_when": [item.model_dump(mode="json") for item in step.done_when],
                "output_contract_ref": step.output_contract_ref,
                "route_decision_id": route_decision_id,
            },
            requested_allowed_tools=allowed,
            requested_deadline=deadline,
            requested_budget=to_delegation_budget(step.budget_limit),
            failure_behavior=step.failure_behavior,
            idempotency_key=f"fanout:{digest}",
            contract_version="orchestration-v1",
        )
        child_run = ChildRun(
            id=child_run_id,
            child_task_id=task_id,
            parent_run_id=parent.id,
            attempt=1,
            status="created",
            phase="created",
            deadline_at=deadline,
            created_at=created_at,
            updated_at=created_at,
        )
        return ChildTaskPackage(
            step_id=step.step_id,
            contract=contract,
            child_run=child_run,
            artifact_refs=tuple(inputs["artifact_refs"]),
            output_contract_ref=step.output_contract_ref,
            trace_context={
                "parent_run_id": parent.id,
                "plan_id": plan.plan_id,
                "step_id": step.step_id,
                "child_run_id": child_run_id,
                "route_decision_id": route_decision_id,
            },
        )


class OrchestrationFanOutService:
    def __init__(self, store: SQLiteRunStore) -> None:
        self._store = store

    def persist(
        self,
        result: FanOutResult,
        *,
        expected_parent_version: int,
        specialist_snapshot_ids: Mapping[str, str],
        output_schema_versions: Mapping[str, str],
        created_at: datetime,
        queue_hard_capacity: int | None = None,
    ) -> tuple[ChildCreationResult, ...]:
        created: list[ChildCreationResult] = []
        version = expected_parent_version
        for package in result.packages:
            specialist_id = package.contract.specialist_id
            item = self._store.create_child_task_and_run(
                contract=package.contract,
                child_run=package.child_run,
                specialist_snapshot_id=specialist_snapshot_ids[specialist_id],
                output_schema_version=output_schema_versions[specialist_id],
                expected_parent_version=version,
                created_at=created_at,
                queue_hard_capacity=queue_hard_capacity,
            )
            created.append(item)
            version = item.parent.version
        return tuple(created)


class FanInGateway:
    """Read only Store-authorized envelope references into the Parent projection."""

    def __init__(self, *, store: SQLiteRunStore, artifact_reader: ResultArtifactReader) -> None:
        self._store = store
        self._artifacts = artifact_reader

    def collect(self, parent_run_id: str) -> tuple[AcceptedChildResult, ...]:
        tree = self._store.get_run_tree(parent_run_id)
        step_by_task = {
            package.contract.task_id: package.step_id
            for package in self._packages_from_parent_state(tree.parent)
        }
        accepted: list[AcceptedChildResult] = []
        for task in tree.child_tasks:
            if task.accepted_child_run_id is None or task.accepted_result_envelope_ref is None:
                continue
            child = next(
                (item for item in tree.child_runs if item.id == task.accepted_child_run_id),
                None,
            )
            if child is None or child.result_hash != task.accepted_result_hash:
                raise ValueError("fanin_accepted_result_authority_invalid")
            artifact = self._artifacts.get_tool_artifact_for_principal(
                task.accepted_result_envelope_ref,
                principal=tree.parent.principal,
            )
            if artifact is None or not isinstance(artifact.get("content"), str):
                raise ValueError("fanin_result_artifact_unavailable")
            envelope = ResultEnvelope.model_validate_json(artifact["content"])
            if (
                envelope.child_run_id != child.id
                or envelope.task_id != task.id
                or envelope.canonical_hash != task.accepted_result_hash
            ):
                raise ValueError("fanin_result_envelope_identity_invalid")
            artifact_refs = tuple(
                str(item["artifact_ref"])
                for item in envelope.evidence
                if isinstance(item.get("artifact_ref"), str)
            )
            step_id = step_by_task.get(task.id) or str(task.constraints_json.get("step_id", task.id))
            projection = OrchestrationContextManager.project_child_result(
                envelope,
                result_envelope_ref=task.accepted_result_envelope_ref,
                artifact_refs=artifact_refs,
            )
            accepted.append(
                AcceptedChildResult(
                    step_id=step_id,
                    child_run_id=child.id,
                    result_envelope_ref=task.accepted_result_envelope_ref,
                    envelope=envelope,
                    projection=projection,
                )
            )
        return tuple(sorted(accepted, key=lambda item: (item.step_id, item.child_run_id)))

    @staticmethod
    def _packages_from_parent_state(parent: ParentRun) -> tuple[ChildTaskPackage, ...]:
        # Current persistence stores the authoritative step_id in ChildTask.constraints.
        # This hook deliberately returns no reconstructed Child context.
        del parent
        return ()


def _find_forbidden_key(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).casefold() in _FORBIDDEN_PACKAGE_KEYS:
                return str(key)
            found = _find_forbidden_key(item)
            if found is not None:
                return found
    elif isinstance(value, (tuple, list)):
        for item in value:
            found = _find_forbidden_key(item)
            if found is not None:
                return found
    return None
