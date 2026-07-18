from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID

import typer
from rich.console import Console
from rich.table import Table

from starter_agent.bootstrap import create_application, get_settings
from starter_agent.domain.errors import AgentError
from starter_agent.providers.registry import ProviderRegistry
from starter_agent.tools.registry import ToolRegistry


app = typer.Typer(help="Starter Agent CLI", no_args_is_help=True)
model_app = typer.Typer(help="Inspect and test model providers.")
tools_app = typer.Typer(help="Inspect tools.")
app.add_typer(model_app, name="model")
app.add_typer(tools_app, name="tools")
console = Console()


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

