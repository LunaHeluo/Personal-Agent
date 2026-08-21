from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import typer
from rich.console import Console
from rich.table import Table

from starter_agent.bootstrap import create_application, get_settings
from starter_agent.domain.errors import AgentError
from starter_agent.providers.registry import ProviderRegistry
from starter_agent.trust.baseline import run_job_research_fixture_baseline
from starter_agent.trust.smoke import (
    DEFAULT_PUBLIC_JD_URL,
    run_job_research_real_smoke,
)
from starter_agent.trust.store import TrustStore
from starter_agent.trust.release_gate import DelegationReleaseDecisionService
from starter_agent.tools.registry import ToolRegistry


app = typer.Typer(help="Starter Agent CLI", no_args_is_help=True)
model_app = typer.Typer(help="Inspect and test model providers.")
tools_app = typer.Typer(help="Inspect tools.")
trust_app = typer.Typer(help="Run Trust Center evals and reports.")
migration_app = typer.Typer(help="Scan, commit, validate and roll back CV workbench migrations.")
app.add_typer(model_app, name="model")
app.add_typer(tools_app, name="tools")
app.add_typer(trust_app, name="trust")
app.add_typer(migration_app, name="migration")
console = Console()


def _migration_service():
    from starter_agent.bootstrap import create_cv_workbench_runtime
    from starter_agent.cv_workbench.migration import LegacyMigrationService

    settings = get_settings()
    return LegacyMigrationService(
        create_cv_workbench_runtime(),
        registry_path=settings.project_root / "data" / "cv-workbench-migrations" / "registry.json",
    )


def _migration_plan(resume_root: Path | None, include_knowledge: bool):
    from starter_agent.knowledge.models import KnowledgeScope

    settings = get_settings()
    scopes = (
        KnowledgeScope(
            user_id=settings.knowledge.default_user_id,
            project_id=settings.knowledge.default_project_id,
        ),
    ) if include_knowledge else ()
    return _migration_service().scan(
        resume_root=resume_root,
        knowledge_scopes=scopes,
        include_research_candidates=True,
    )


@migration_app.command("scan")
def migration_scan(
    resume_root: Path | None = typer.Option(None, "--resume-root", help="Legacy ResumeManager storage root."),
    include_knowledge: bool = typer.Option(False, "--include-knowledge"),
    report: Path | None = typer.Option(None, "--report", help="Optional JSON preview report path."),
) -> None:
    """Read legacy sources and print a dry-run preview; no business objects are written."""
    service = _migration_service()
    plan = _migration_plan(resume_root, include_knowledge)
    payload = service.plan_dict(plan)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if report is not None:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(rendered, encoding="utf-8")
    console.print_json(rendered)


@migration_app.command("commit")
def migration_commit(
    workspace_id: str = typer.Option(..., "--workspace-id"),
    batch_id: str = typer.Option(..., "--batch-id"),
    resume_root: Path | None = typer.Option(None, "--resume-root"),
    include_knowledge: bool = typer.Option(False, "--include-knowledge"),
) -> None:
    """Commit ready candidates from a fresh scan; safe to resume with the same batch id."""
    settings = get_settings(); service = _migration_service()
    result = service.commit(
        _migration_plan(resume_root, include_knowledge),
        batch_id=batch_id,
        workspace_id=workspace_id,
        principal=settings.knowledge.default_user_id,
    )
    console.print_json(json.dumps(result, ensure_ascii=False))


@migration_app.command("validate")
def migration_validate(batch_id: str = typer.Option(..., "--batch-id")) -> None:
    """Validate that every committed target still exists in the trusted principal scope."""
    settings = get_settings(); result = _migration_service().validate(batch_id, principal=settings.knowledge.default_user_id)
    console.print_json(json.dumps(result, ensure_ascii=False))
    if not result["valid"]: raise typer.Exit(1)


@migration_app.command("rollback")
def migration_rollback(batch_id: str = typer.Option(..., "--batch-id")) -> None:
    """Remove only unreferenced mappings created by this batch; legacy sources remain."""
    settings = get_settings(); result = _migration_service().rollback(batch_id, principal=settings.knowledge.default_user_id)
    console.print_json(json.dumps(result, ensure_ascii=False))
    if result["status"] == "rollback_partial": raise typer.Exit(2)


@app.command()
def doctor() -> None:
    """Check local configuration and required files."""
    settings = get_settings()
    checks = [
        ("Config", True, "loaded"),
        (
            "Identity",
            settings.resolve_path(settings.app.identity_path).exists(),
            settings.app.identity_path,
        ),
        (
            "Data directory",
            settings.resolve_path("data").is_dir(),
            str(settings.resolve_path("data")),
        ),
        (
            "Default provider",
            settings.model.default_provider in settings.providers,
            settings.model.default_provider,
        ),
    ]
    table = Table("Check", "Status", "Detail")
    failed = False
    for name, ok, detail in checks:
        failed = failed or not ok
        table.add_row(name, "[green]OK[/green]" if ok else "[red]FAIL[/red]", detail)
    console.print(table)
    if failed:
        raise typer.Exit(1)


@app.command()
def chat(
    message: str | None = typer.Argument(None, help="One-shot message."),
    provider: str | None = typer.Option(None, "--provider", "-p"),
    model: str | None = typer.Option(None, "--model", "-m"),
    session: UUID | None = typer.Option(None, "--session", "-s"),
) -> None:
    """Chat once or enter an interactive session."""

    async def send(text: str, current_session: UUID | None) -> UUID:
        try:
            result = await create_application().chat(
                text,
                session_id=current_session,
                provider_name=provider,
                model=model,
            )
        except AgentError as exc:
            console.print(f"[red]{exc.code}:[/red] {exc}")
            raise typer.Exit(1) from exc
        console.print(f"[bold cyan]Agent:[/bold cyan] {result.content}")
        console.print(
            f"[dim]session={result.session_id} turn={result.turn_id} "
            f"provider={result.provider} model={result.model} tools={result.tool_calls}[/dim]"
        )
        return result.session_id

    async def run() -> None:
        current_session = session
        if message:
            await send(message, current_session)
            return
        console.print("Starter Agent interactive chat. Type /exit to quit.")
        while True:
            text = console.input("[bold green]You:[/bold green] ").strip()
            if text in {"/exit", "/quit"}:
                return
            if text:
                current_session = await send(text, current_session)

    asyncio.run(run())


@model_app.command("list")
def model_list() -> None:
    """List configured providers without exposing secrets."""
    settings = get_settings()
    table = Table("Provider", "Type", "Key")
    for name in sorted(settings.providers):
        config = settings.providers[name]
        key_status = (
            "not required"
            if config.type == "mock"
            else ("set" if settings.provider_api_key(name) else "missing")
        )
        table.add_row(name, config.type, key_status)
    console.print(table)


@model_app.command("test")
def model_test(
    provider: str | None = typer.Option(None, "--provider", "-p"),
    model: str | None = typer.Option(None, "--model", "-m"),
) -> None:
    """Send a minimal health request to a provider."""
    settings = get_settings()
    provider_name = provider or settings.model.default_provider
    model_name = model or settings.model.default_model

    async def run() -> None:
        try:
            target = ProviderRegistry(settings).get(provider_name)
            ok, detail = await target.health(model_name)
        except AgentError as exc:
            ok, detail = False, str(exc)
        console.print(("[green]OK[/green] " if ok else "[red]FAIL[/red] ") + detail)
        if not ok:
            raise typer.Exit(1)

    asyncio.run(run())


@tools_app.command("list")
def tools_list() -> None:
    """List enabled tools and their risk levels."""
    settings = get_settings()
    table = Table("Tool", "Risk", "Description")
    for tool in ToolRegistry(settings.tools.enabled).list():
        table.add_row(tool.name, tool.risk_level, tool.description)
    console.print(table)


@trust_app.command("fixture-baseline")
def trust_fixture_baseline(
    run_id: str = typer.Option(..., "--run-id", help="Stable eval run id."),
    project_root: Path | None = typer.Option(None, "--project-root"),
    database_url: str | None = typer.Option(None, "--database-url"),
    report_dir: Path | None = typer.Option(None, "--report-dir"),
    known_failure_case_id: str | None = typer.Option(
        None,
        "--known-failure-case-id",
        help="Optional deterministic hard-gate failure used for regression drills.",
    ),
    variant: str = typer.Option(
        "multi_agent",
        "--variant",
        help="Paired fixture projection: single_agent or multi_agent.",
    ),
) -> None:
    """Run fixed job-research fixtures; never performs live web/model calls."""

    if variant not in {"single_agent", "multi_agent"}:
        raise typer.BadParameter("variant must be single_agent or multi_agent")
    settings = get_settings()
    root = (project_root or settings.project_root).resolve()
    store = TrustStore(database_url or settings.app.database_url, root)
    report = run_job_research_fixture_baseline(
        store=store,
        project_root=root,
        run_id=run_id,
        report_dir=report_dir or (root / "reports" / "trust"),
        known_failure_case_id=known_failure_case_id,
        evaluation_variant=variant,
    )
    console.print(
        "[green]Trust fixture baseline recorded[/green] "
        f"run={report['run_id']} gate={report['gate']['status']} "
        f"cases={report['case_count']} report={report['report_path']}"
    )


@trust_app.command("delegation-compare")
def trust_delegation_compare(
    baseline_report: Path = typer.Option(..., "--baseline-report"),
    candidate_report: Path = typer.Option(..., "--candidate-report"),
    decision_id: str = typer.Option(..., "--decision-id"),
    expires_at: str = typer.Option(..., "--expires-at", help="ISO-8601 expiry."),
    project_root: Path | None = typer.Option(None, "--project-root"),
    database_url: str | None = typer.Option(None, "--database-url"),
) -> None:
    """Persist a hash-bound Task20 default-route decision from two reports."""
    try:
        baseline = json.loads(baseline_report.read_text(encoding="utf-8"))
        candidate = json.loads(candidate_report.read_text(encoding="utf-8"))
        expiry = datetime.fromisoformat(expires_at)
        if expiry.tzinfo is None:
            raise ValueError("expiry timezone required")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    settings = get_settings()
    root = (project_root or settings.project_root).resolve()
    decision = DelegationReleaseDecisionService(
        TrustStore(database_url or settings.app.database_url, root)
    ).compare_and_persist(
        baseline_report=baseline,
        candidate_report=candidate,
        decision_id=decision_id,
        created_at=datetime.now(UTC),
        expires_at=expiry,
    )
    console.print(
        f"decision={decision_id} status={decision['status']} "
        f"hash={decision['decision_hash']}"
    )


@trust_app.command("real-smoke")
def trust_real_smoke(
    run_id: str = typer.Option(..., "--run-id", help="Stable smoke run id."),
    source_url: str = typer.Option(DEFAULT_PUBLIC_JD_URL, "--source-url"),
    provider: str | None = typer.Option(None, "--provider", "-p"),
    model: str | None = typer.Option(None, "--model", "-m"),
    project_root: Path | None = typer.Option(None, "--project-root"),
    database_url: str | None = typer.Option(None, "--database-url"),
    report_dir: Path | None = typer.Option(None, "--report-dir"),
) -> None:
    """Run live model + Playwright MCP smoke; never mixes into fixture baseline."""

    settings = get_settings()
    root = (project_root or settings.project_root).resolve()
    store = TrustStore(database_url or settings.app.database_url, root)

    async def run() -> None:
        report = await run_job_research_real_smoke(
            settings=settings,
            trust_store=store,
            project_root=root,
            run_id=run_id,
            report_dir=report_dir or (root / "reports" / "trust"),
            source_url=source_url,
            provider_name=provider,
            model_name=model,
        )
        console.print(
            "[green]Trust real smoke recorded[/green] "
            f"run={report['run_id']} status={report['status']} "
            f"source={report['source_url']} report={report['report_path']}"
        )
        if report["status"] != "passed":
            raise typer.Exit(code=2)

    asyncio.run(run())


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8000),
    reload: bool = typer.Option(False),
) -> None:
    """Run the FastAPI server."""
    import uvicorn

    uvicorn.run(
        "starter_agent.interfaces.api:app",
        host=host,
        port=port,
        reload=reload,
    )
