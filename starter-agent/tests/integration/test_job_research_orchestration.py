import ipaddress
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from starter_agent.capabilities.gate import (
    NetworkGuardAttestation,
    PreToolCallGate,
    UnifiedToolExecutor,
)
from starter_agent.capabilities.models import (
    PolicyRule,
    Server,
    Snapshot,
    Tool as McpTool,
    canonical_json_sha256,
)
from starter_agent.capabilities.policy import BrowserScopePolicy
from starter_agent.capabilities.registry import UnifiedToolRegistry
from starter_agent.capabilities.store import CapabilityStore
from starter_agent.domain.models import ToolResult
from starter_agent.skills.job_research import JobResearchOrchestrator
from starter_agent.tools.base import Tool, ToolContext
from starter_agent.tools.builtin.knowledge import RetrieveResumeEvidenceTool


async def _public_resolver(_host: str):
    return [ipaddress.ip_address("93.184.216.34")]


class _Builtins:
    email_manager = None

    def __init__(self, tools):
        self._tools = tools

    def list(self):
        return list(self._tools)


class _SearchTool(Tool):
    name = "search_jobs_serpapi"
    description = "Search jobs"
    risk_level = "read"
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "location": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    async def execute(self, arguments, context):
        return ToolResult(
            ok=True,
            data={
                "results": [
                    {
                        "title": "AI Agent Engineer",
                        "company": "Example",
                        "location": "Shanghai",
                        "url": "https://jobs.example/agent",
                    }
                ]
            },
        )


class _Knowledge:
    scope = SimpleNamespace(user_id="local-user", project_id="project")

    def retrieve(self, knowledge_base_id, question, **filters):
        return [
            SimpleNamespace(
                chunk_id=uuid4(),
                document_id=uuid4(),
                filename="resume.md",
                version=1,
                section_path=["Projects"],
                start_line=4,
                end_line=4,
                preview="Built a production RAG agent with Python.",
                source_ref="resume.md@v1#L4-L4",
                rank=1,
            )
        ]


async def test_job_research_calls_every_real_tool_through_gate_and_keeps_trace(tmp_path):
    search = _SearchTool()
    rag = RetrieveResumeEvidenceTool(_Knowledge())
    builtins = _Builtins([search, rag])
    registry = UnifiedToolRegistry(builtins)
    browser_schema = {
        "type": "object",
        "properties": {"url": {"type": "string"}},
        "required": ["url"],
        "additionalProperties": False,
    }
    browser = McpTool(
        snapshot_id="playwright-snapshot-1",
        server_id="playwright",
        upstream_name="browser_navigate",
        model_alias="mcp__playwright__browser_navigate",
        description="Read a public job page",
        input_schema=browser_schema,
        schema_hash=canonical_json_sha256(browser_schema),
        enabled=True,
        review_state="approved",
        risk_level="read",
        metadata={"browser": True, "action": "navigate"},
    )
    server = Server(
        id="playwright",
        name="playwright",
        config_source="config/mcp.json",
        config_hash="a" * 64,
        enabled=True,
        connection_state="ready",
    )
    registry.refresh_server(
        server,
        [browser],
    )
    store = CapabilityStore("sqlite:///:memory:", tmp_path)
    store.create_server(server)
    store.create_snapshot(
        Snapshot(
            id=browser.snapshot_id,
            server_id=server.id,
            version=1,
            schema_hash=browser.schema_hash,
            discovered_at=datetime.now(UTC),
            tool_count=1,
        ),
        tools=[browser],
    )
    store.activate_snapshot(server.id, browser.snapshot_id)
    for server_id, name, schema_hash in (
        ("builtin", search.name, canonical_json_sha256(search.input_schema)),
        ("builtin", rag.name, canonical_json_sha256(rag.input_schema)),
        ("playwright", browser.upstream_name, browser.schema_hash),
    ):
        store.create_policy_rule(
            PolicyRule(
                id=f"allow-{server_id}-{name}",
                server_id=server_id,
                tool_name=name,
                effect="allowlist_auto",
                actions=("read", "navigate"),
                schema_hash=schema_hash,
                created_by="test",
            )
        )
    gate = PreToolCallGate(
        store,
        registry=registry,
        browser_policy=BrowserScopePolicy(resolver=_public_resolver),
    )
    executor = UnifiedToolExecutor(store, gate=gate)
    invoked = []

    async def invoke_builtin(arguments, context, *, tool):
        invoked.append(tool.name)
        return await tool.execute(dict(arguments), context)

    for tool in (search, rag):
        executor.register_invoker(
            server_id="builtin",
            tool_name=tool.name,
            invoker=lambda arguments, context, tool=tool: invoke_builtin(
                arguments, context, tool=tool
            ),
            context_factory=lambda request: ToolContext(
                session_id=uuid4(),
                turn_id=uuid4(),
                tool_call_id=request.call_id,
                user_id="local-user",
                project_id="project",
                knowledge_base_id=knowledge_base_id,
            ),
        )

    async def invoke_browser(arguments, _context):
        invoked.append("browser_navigate")
        return ToolResult(
            ok=True,
            data={
                "title": "AI Agent Engineer",
                "company": "Example",
                "location": "Shanghai",
                "responsibilities": ["Build agent systems"],
                "requirements": ["Python", "RAG"],
                "source_url": arguments["url"],
                "retrieved_at": "2026-07-23T00:00:00+00:00",
            },
            metadata={"is_untrusted_external_content": True},
        )

    async def attest(request):
        return NetworkGuardAttestation(
            targets=(request.arguments["url"],),
            dns_pinned=True,
            redirects_enforced=True,
            peer_verified=True,
        )

    executor.register_invoker(
        server_id="playwright",
        tool_name="browser_navigate",
        invoker=invoke_browser,
        network_guard=attest,
    )
    orchestrator = JobResearchOrchestrator(registry, executor)
    session_id, turn_id, knowledge_base_id = uuid4(), uuid4(), uuid4()
    context = ToolContext(
        session_id=session_id,
        turn_id=turn_id,
        user_id="local-user",
        project_id="project",
        knowledge_base_id=knowledge_base_id,
    )

    search_result = await orchestrator.search(
        query="AI Agent engineer",
        location="Shanghai",
        limit=1,
        context=context,
    )
    result = await orchestrator.analyze(
        query="AI Agent engineer",
        selected_url=search_result.data["results"][0]["url"],
        context=context,
    )

    assert search_result.status == "waiting_for_url_selection"
    assert (
        result.status == "waiting_for_jd_ingestion_confirmation"
    ), result.model_dump()
    assert invoked == [
        "search_jobs_serpapi",
        "browser_navigate",
        "retrieve_resume_evidence",
    ]
    assert [item.tool_name for item in (*search_result.trace, *result.trace)] == [
        "search_jobs_serpapi",
        "mcp__playwright__browser_navigate",
        "retrieve_resume_evidence",
    ]
    assert result.data["job"]["source_url"] == "https://jobs.example/agent"
    assert result.data["resume_evidence"][0]["quote"].startswith("Built")
    assert result.data["ingestion"]["status"] == "confirmation_required"
    assert all(item.result is not None for item in result.trace)
    assert len(
        [
            event
            for event in store.list_audit_events()
            if event.action == "tool.invoked" and event.decision == "allow"
        ]
    ) == 3


async def test_missing_dependency_fails_closed_without_tool_invocation(tmp_path):
    registry = UnifiedToolRegistry(_Builtins([]))
    store = CapabilityStore("sqlite:///:memory:", tmp_path)
    gate = PreToolCallGate(store, registry=registry)
    orchestrator = JobResearchOrchestrator(
        registry,
        UnifiedToolExecutor(store, gate=gate),
    )

    result = await orchestrator.search(
        query="AI engineer",
        context=ToolContext(session_id=uuid4(), turn_id=uuid4()),
    )

    assert result.status == "dependency_unavailable"
    assert result.error_code == "dependency_unavailable"
    assert result.missing_dependencies == ("tool:search_jobs_serpapi",)
    assert result.trace == ()
    assert store.list_audit_events() == []
