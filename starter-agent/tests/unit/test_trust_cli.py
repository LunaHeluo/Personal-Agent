from pathlib import Path
from uuid import uuid4

from typer.testing import CliRunner

from starter_agent.interfaces.cli import app


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SESSION_ONLY_ROOT = PROJECT_ROOT / ".session-only-trust-cli-tests"


def test_trust_fixture_baseline_cli_writes_report() -> None:
    run_id = f"cli-fixture-{uuid4().hex[:8]}"
    db_path = SESSION_ONLY_ROOT / uuid4().hex / "agent.db"
    report_dir = SESSION_ONLY_ROOT / "reports"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "trust",
            "fixture-baseline",
            "--project-root",
            str(PROJECT_ROOT),
            "--database-url",
            f"sqlite:///{db_path}",
            "--report-dir",
            str(report_dir),
            "--run-id",
            run_id,
        ],
    )

    assert result.exit_code == 0, result.output
    assert run_id in result.output
    assert (report_dir / f"{run_id}.json").exists()
