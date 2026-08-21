"""Read-only compatibility view for the retired synchronous job workflow."""

from __future__ import annotations

import json
from uuid import UUID, uuid4

from starter_agent.domain.models import ChatResult


class LegacyJobResearchCompatibilityAdapter:
    """Render persisted delegation state without executing tools or writes."""

    def __init__(self, *, run_store, artifact_store) -> None:
        self.run_store = run_store
        self.artifact_store = artifact_store

    def to_chat_result(
        self,
        *,
        parent_run_id: str,
        session_id: UUID,
        provider: str,
        model: str,
    ) -> ChatResult:
        tree = self.run_store.get_run_tree(parent_run_id)
        parent = tree.parent
        web_task = next(
            (
                task for task in tree.child_tasks
                if task.specialist_id == "job_web_researcher"
            ),
            None,
        )
        child_run_id = None if web_task is None else web_task.accepted_child_run_id
        if web_task is None or web_task.accepted_result_envelope_ref is None:
            return self._result(
                session_id=session_id,
                provider=provider,
                model=model,
                parent=parent,
                task_id=None if web_task is None else web_task.id,
                child_run_id=child_run_id,
                content="岗位调研任务仍在处理中。",
            )
        artifact = self.artifact_store.get_tool_artifact_for_principal(
            web_task.accepted_result_envelope_ref,
            principal=parent.principal,
        )
        raw = None if artifact is None else artifact.get("content")
        if not isinstance(raw, str):
            return self._result(
                session_id=session_id,
                provider=provider,
                model=model,
                parent=parent,
                task_id=web_task.id,
                child_run_id=child_run_id,
                content="岗位调研结果暂不可读取。",
            )
        try:
            envelope = json.loads(raw)
            output = envelope.get("output", {})
            if not isinstance(output, dict):
                raise ValueError("output")
        except (ValueError, TypeError, json.JSONDecodeError):
            return self._result(
                session_id=session_id,
                provider=provider,
                model=model,
                parent=parent,
                task_id=web_task.id,
                child_run_id=child_run_id,
                content="岗位调研结果格式不可用。",
            )
        jobs = output.get("jobs")
        missing = output.get("missing")
        verified = len(jobs) if isinstance(jobs, list) else 0
        missing_count = len(missing) if isinstance(missing, list) else 0
        state = "部分完成" if envelope.get("status") == "partial" else "已完成"
        return self._result(
            session_id=session_id,
            provider=provider,
            model=model,
            parent=parent,
            task_id=web_task.id,
            child_run_id=child_run_id,
            content=f"岗位调研{state}：已验证 JD {verified} 个；缺失项 {missing_count} 个。",
        )

    @staticmethod
    def _result(*, session_id, provider, model, parent, task_id, child_run_id, content):
        return ChatResult(
            session_id=session_id,
            turn_id=uuid4(),
            content=content,
            provider=provider,
            model=model,
            parent_run_id=parent.id,
            child_task_id=task_id,
            child_run_id=child_run_id,
            route=parent.route,
            legacy_path_used=False,
        )
