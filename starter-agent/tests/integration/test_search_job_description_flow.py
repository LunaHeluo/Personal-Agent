import json
import re
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from starter_agent.agent.context import ContextBuilder
from starter_agent.agent.runtime import AgentRuntime
from starter_agent.application import ApplicationService
from starter_agent.domain.models import Message, ModelResponse, ToolCall, ToolResult
from starter_agent.infrastructure.session_store import SQLiteSessionStore
from starter_agent.providers.base import Provider
from starter_agent.settings import ContextConfig, RuntimeConfig, load_settings
from starter_agent.tools.base import Tool, ToolContext
from starter_agent.tools.policy import ToolPolicy


class _ToolRegistry:
    def __init__(self, *tools: Tool) -> None:
        self.tools = {tool.name: tool for tool in tools}

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self.tools.values()]


class _SearchTool(Tool):
    name = "search_jobs_serpapi"
    description = "offline job search"
    risk_level = "read"
    input_schema: dict[str, Any] = {"type": "object"}

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult(
            ok=True,
            data={
                "results": [
                    {"url": "https://jobs.example/first", "title": "First", "company": "Alpha"},
                    {"url": "https://jobs.example/second", "title": "Second", "company": "Beta"},
                ]
            },
        )


class _JobDescriptionTool(Tool):
    name = "search_job_description"
    description = "offline job description"
    risk_level = "read"
    input_schema: dict[str, Any] = {"type": "object"}

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        self.calls.append(arguments)
        return ToolResult(
            ok=True,
            data={"raw_text": "JD detail " * 2_000},
            metadata={"is_untrusted_external_content": True},
        )


class _SelectionProvider(Provider):
    name = "selection"

    async def complete(self, messages, model, tools, on_delta=None, tool_choice=None):
        last = messages[-1]
        if last.role == "tool":
            return ModelResponse(content="done", provider=self.name, model=model)
        if last.content == "search":
            return ModelResponse(
                provider=self.name,
                model=model,
                tool_calls=[ToolCall(id="search-1", name="search_jobs_serpapi", arguments={"query": "AI"})],
            )
        match = re.fullmatch(r"第\s*(\d+)\s*个", last.content)
        if match is None:
            return ModelResponse(
                content="请选择第 N 个岗位。", provider=self.name, model=model
            )
        search_result = next(
            message for message in reversed(messages)
            if message.role == "tool" and message.name == "search_jobs_serpapi"
        )
        results = json.loads(search_result.content)["data"]["results"]
        selection_index = int(match.group(1))
        selected = results[selection_index - 1]
        return ModelResponse(
            provider=self.name,
            model=model,
            tool_calls=[ToolCall(
                id="job-2",
                name="search_job_description",
                arguments={
                    "url": selected["url"],
                    "expected_title": selected["title"],
                    "expected_company": selected["company"],
                    "source_ref": f"search_result:{selection_index}",
                },
            )],
        )

    async def health(self, model: str) -> tuple[bool, str]:
        return True, "ready"


async def _search_then_select(governance_enabled: bool, selection: str = "第 2 个"):
    job_tool = _JobDescriptionTool()
    runtime = AgentRuntime(
        _ToolRegistry(_SearchTool(), job_tool),  # type: ignore[arg-type]
        ToolPolicy(["read"]),
        RuntimeConfig(),
        ContextConfig(per_tool_result_tokens=300, all_tool_results_tokens=300),
    )
    provider = _SelectionProvider()
    messages = [Message(role="user", content="search")]
    session_id, first_turn = uuid4(), uuid4()
    _, first_generated, _ = await runtime.run(
        provider, "offline", messages, session_id, first_turn
    )
    messages.extend(first_generated)
    messages.append(Message(role="user", content=selection))
    events: list[dict[str, Any]] = []

    async def on_event(event: dict[str, Any]) -> None:
        events.append(event)

    result, generated, calls = await runtime.run(
        provider, "offline", messages, session_id, uuid4(),
        on_tool_event=on_event,
        tool_governance_enabled=governance_enabled,
    )
    return job_tool, events, generated, result, calls


@pytest.mark.parametrize(
    ("selection", "expected", "source_ref"),
    [
        ("第 1 个", ("https://jobs.example/first", "First", "Alpha"), "search_result:1"),
        ("第 2 个", ("https://jobs.example/second", "Second", "Beta"), "search_result:2"),
    ],
)
async def test_selection_text_uses_the_matching_previous_result(
    selection: str, expected: tuple[str, str, str], source_ref: str
) -> None:
    job_tool, events, generated, result, calls = await _search_then_select(True, selection)

    assert job_tool.calls == [{
        "url": expected[0],
        "expected_title": expected[1],
        "expected_company": expected[2],
        "source_ref": source_ref,
    }]
    assert result.content == "done"
    assert calls == 1
    assert [event["type"] for event in events] == ["tool_started", "tool_completed"]
    completed = events[-1]
    assert completed["tool_governance_enabled"] is True
    assert completed["is_truncated"] is True
    assert completed["raw_result_tokens"] > completed["context_result_tokens"]
    payload = json.loads(next(message for message in generated if message.role == "tool").content)
    assert payload["metadata"]["is_untrusted_external_content"] is True


async def test_disabling_governance_keeps_the_full_job_result() -> None:
    _, events, generated, _, _ = await _search_then_select(False)

    completed = events[-1]
    assert completed["tool_governance_enabled"] is False
    assert completed["is_truncated"] is False
    assert completed["raw_result_tokens"] == completed["context_result_tokens"]
    payload = json.loads(next(message for message in generated if message.role == "tool").content)
    assert len(payload["data"]["raw_text"]) > 10_000
    assert payload["metadata"]["is_untrusted_external_content"] is True


class _Providers:
    def __init__(self, provider: Provider) -> None:
        self.provider = provider

    def get(self, name: str) -> Provider:
        return self.provider


async def test_selected_jd_is_persisted_only_in_session_and_creates_no_job_record() -> None:
    root = Path.cwd() / f".session-only-{uuid4()}"
    root.mkdir()
    try:
        (root / "agent.md").write_text("# Test Agent", encoding="utf-8")
        (root / "system.md").write_text("{identity}", encoding="utf-8")
        settings = load_settings("config/config.example.yaml")
        settings.providers["mock"].models = ["starter-mock"]
        settings.project_root = root
        settings.app.database_url = "sqlite:///agent.db"
        settings.app.identity_path = "agent.md"
        settings.memory.auto_write_enabled = False
        job_tool = _JobDescriptionTool()
        runtime = AgentRuntime(
            _ToolRegistry(_SearchTool(), job_tool),  # type: ignore[arg-type]
            ToolPolicy(["read"]),
            RuntimeConfig(),
            ContextConfig(per_tool_result_tokens=300, all_tool_results_tokens=300),
        )
        application = ApplicationService(
            settings,
            SQLiteSessionStore(settings.app.database_url, root),
            _Providers(_SelectionProvider()),  # type: ignore[arg-type]
            runtime,
            ContextBuilder(root / "agent.md", root / "system.md"),
        )

        first = await application.chat("search", provider_name="mock")
        second = await application.chat(
            "第 2 个", session_id=first.session_id, provider_name="mock"
        )

        assert second.tool_calls == 1
        assert job_tool.calls[-1]["url"] == "https://jobs.example/second"
        stored = application.store.list_messages(first.session_id)
        assert any(message.name == "search_job_description" for message in stored)
        assert not (root / "data" / "jobs").exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)
