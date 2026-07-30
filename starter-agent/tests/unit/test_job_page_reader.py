from uuid import uuid4

from starter_agent.domain.models import ToolResult
from starter_agent.job_research.page_reader import PlaywrightJobPageReader
from starter_agent.skills.models import SkillToolTrace
from starter_agent.tools.base import ToolContext


URL = "https://jobs.example/role"


def _trace(tool_name: str, arguments: dict) -> SkillToolTrace:
    return SkillToolTrace(
        tool_name=tool_name,
        call_id=f"call-{uuid4().hex}",
        arguments=arguments,
        result={"ok": True},
        gate_outcome="allow",
    )


def _snapshot(content_hash: str, *, chars: int = 900) -> ToolResult:
    return ToolResult(
        ok=True,
        data={
            "structured_content": {
                "title": "Agent Engineer",
                "company": "Example",
                "location": "Beijing",
                "responsibilities": ["Build agent workflows"],
                "requirements": ["Production Python experience"],
                "source_url": URL,
                "page_type": "job_detail",
                "validation_state": "verified",
            }
        },
        metadata={
            "source_url": URL,
            "final_url": URL,
            "source_content_sha256": content_hash,
            "snapshot_chars": chars,
        },
    )


def _context() -> ToolContext:
    return ToolContext(session_id=uuid4(), turn_id=uuid4())


async def _no_sleep(_seconds: float) -> None:
    return None


async def test_reader_waits_15_seconds_and_requires_two_stable_snapshots() -> None:
    calls: list[tuple[str, dict]] = []

    async def call(tool_name, arguments, _context):
        calls.append((tool_name, arguments))
        if tool_name.endswith("browser_navigate"):
            result = ToolResult(
                ok=True,
                data={"source_url": URL},
                metadata={"source_url": URL, "final_url": URL},
            )
        elif tool_name.endswith("browser_wait_for"):
            result = ToolResult(ok=True)
        else:
            result = _snapshot("a" * 64)
        return result, _trace(tool_name, arguments)

    result = await PlaywrightJobPageReader(call, sleeper=_no_sleep).read(
        URL,
        _context(),
    )

    assert result.ok
    assert [name for name, _arguments in calls] == [
        "mcp__playwright__browser_navigate",
        "mcp__playwright__browser_wait_for",
        "mcp__playwright__browser_snapshot",
        "mcp__playwright__browser_snapshot",
    ]
    assert calls[1][1] == {"time": 15}
    assert len(result.attempts) == 1
    assert result.attempts[0].wait_seconds == 15
    assert result.attempts[0].wait_method == "playwright"


async def test_reader_retries_empty_snapshot_once_with_30_second_wait() -> None:
    waits: list[dict] = []
    navigate_count = 0
    snapshot_count = 0

    async def call(tool_name, arguments, _context):
        nonlocal navigate_count, snapshot_count
        if tool_name.endswith("browser_navigate"):
            navigate_count += 1
            result = ToolResult(
                ok=True,
                data={"source_url": URL},
                metadata={"source_url": URL, "final_url": URL},
            )
        elif tool_name.endswith("browser_wait_for"):
            waits.append(arguments)
            result = ToolResult(ok=True)
        else:
            snapshot_count += 1
            result = (
                _snapshot("empty", chars=141)
                if snapshot_count <= 2
                else _snapshot("b" * 64)
            )
        return result, _trace(tool_name, arguments)

    result = await PlaywrightJobPageReader(call, sleeper=_no_sleep).read(
        URL,
        _context(),
    )

    assert result.ok
    assert waits == [{"time": 15}, {"time": 30}]
    assert navigate_count == 2
    assert snapshot_count == 4
    assert [attempt.error_code for attempt in result.attempts] == [
        "selector_unmatched",
        None,
    ]


async def test_reader_uses_client_timer_when_wait_tool_is_unavailable() -> None:
    slept: list[float] = []
    called_tools: list[str] = []

    async def sleeper(seconds: float) -> None:
        slept.append(seconds)

    async def call(tool_name, arguments, _context):
        called_tools.append(tool_name)
        if tool_name.endswith("browser_navigate"):
            result = ToolResult(
                ok=True,
                data={"source_url": URL},
                metadata={"source_url": URL, "final_url": URL},
            )
        else:
            result = _snapshot("c" * 64)
        return result, _trace(tool_name, arguments)

    result = await PlaywrightJobPageReader(
        call,
        wait_tool_available=False,
        sleeper=sleeper,
    ).read(URL, _context())

    assert result.ok
    assert slept == [15, 1]
    assert not any(name.endswith("browser_wait_for") for name in called_tools)
    assert result.attempts[0].wait_method == "client_timer"


async def test_reader_does_not_retry_more_than_once() -> None:
    navigate_count = 0

    async def call(tool_name, arguments, _context):
        nonlocal navigate_count
        if tool_name.endswith("browser_navigate"):
            navigate_count += 1
            result = ToolResult(
                ok=False,
                error_code="playwright_timeout",
                display="navigation timed out",
            )
        else:
            result = ToolResult(ok=True)
        return result, _trace(tool_name, arguments)

    result = await PlaywrightJobPageReader(call).read(URL, _context())

    assert not result.ok
    assert result.error_code == "playwright_timeout"
    assert navigate_count == 2
    assert len(result.attempts) == 2
