import json
from pathlib import Path

import pytest

from starter_agent.mcp.config import McpConfigError, McpConfigLoader
from starter_agent.settings import load_settings


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _write_config(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_loads_playwright_config_referenced_by_main_settings() -> None:
    settings = load_settings(PROJECT_ROOT / "config" / "config.yaml")

    config = McpConfigLoader(settings.project_root).load(settings.mcp.config_path)

    playwright = config.servers["playwright"]
    assert playwright.command == "npx"
    assert playwright.args == ("@playwright/mcp@latest",)
    assert playwright.cwd is None
    assert config.source_path == PROJECT_ROOT / "config" / "mcp.json"


def test_config_hash_is_canonical_and_changes_with_meaningful_content(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.json"
    reordered = tmp_path / "reordered.json"
    changed = tmp_path / "changed.json"
    _write_config(
        first,
        {
            "mcpServers": {
                "playwright": {
                    "command": "npx",
                    "args": ["@playwright/mcp@latest"],
                }
            }
        },
    )
    reordered.write_text(
        '{\n  "mcpServers": {"playwright": {'
        '"args": ["@playwright/mcp@latest"], "command": "npx"}}\n}',
        encoding="utf-8",
    )
    _write_config(
        changed,
        {
            "mcpServers": {
                "playwright": {
                    "command": "npx",
                    "args": ["@playwright/mcp@1.0.0"],
                }
            }
        },
    )
    loader = McpConfigLoader(tmp_path)

    first_config = loader.load(first)
    reordered_config = loader.load(reordered)
    changed_config = loader.load(changed)

    assert first_config.config_hash == reordered_config.config_hash
    assert first_config.config_hash != changed_config.config_hash
    assert len(first_config.config_hash) == 64


def test_rejects_config_path_escape_and_inline_secrets(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    outside = tmp_path / "outside.json"
    _write_config(outside, {"mcpServers": {}})
    loader = McpConfigLoader(project_root)

    with pytest.raises(McpConfigError, match="outside project root"):
        loader.load(outside)

    secret_cases = (
        {"env": {"API_TOKEN": "inline-token"}},
        {"command": "npx --token=inline-token"},
        {"args": ["--password=hunter2"]},
        {"args": ["--cookie", "session=inline-cookie"]},
    )
    for index, secret_fields in enumerate(secret_cases):
        path = project_root / f"secret-{index}.json"
        server = {"command": "npx", **secret_fields}
        _write_config(path, {"mcpServers": {"unsafe": server}})
        with pytest.raises(McpConfigError, match="secret|environment names"):
            loader.load(path)


def test_rejects_cwd_escape_unknown_fields_and_control_characters(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    loader = McpConfigLoader(project_root)
    invalid_servers = (
        {"command": "npx", "cwd": "../outside"},
        {"command": "npx", "shell": True},
        {"command": "npx\nwhoami"},
    )

    for index, server in enumerate(invalid_servers):
        path = project_root / f"invalid-{index}.json"
        _write_config(path, {"mcpServers": {"unsafe": server}})
        with pytest.raises(McpConfigError):
            loader.load(path)
