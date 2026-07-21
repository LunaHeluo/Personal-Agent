from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from starter_agent.domain.errors import ConfigurationError


_SERVER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_SECRET_ARGUMENT = re.compile(
    r"(?:api[_-]?key|authorization|bearer|cookie|credential|pass(?:word|wd)?|secret|token)",
    flags=re.IGNORECASE,
)
_SECRET_VALUE = re.compile(r"(?:sk-[A-Za-z0-9_-]{8,}|eyJ[A-Za-z0-9_-]{8,})")


class McpConfigError(ConfigurationError):
    """Raised when an MCP launch configuration violates its trust boundary."""


def _contains_control_characters(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


class McpServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    command: str = Field(min_length=1, max_length=500)
    args: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    cwd: Path | None = None
    env: tuple[str, ...] = Field(default_factory=tuple, max_length=100)

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: str) -> str:
        if _contains_control_characters(value):
            raise ValueError("MCP command contains control characters")
        normalized = value.strip()
        if not normalized:
            raise ValueError("MCP command must not be blank")
        if _SECRET_ARGUMENT.search(normalized) or _SECRET_VALUE.search(normalized):
            raise ValueError("MCP command must not contain inline secrets")
        return normalized

    @field_validator("args")
    @classmethod
    def validate_arguments(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            if not value or len(value) > 2_000:
                raise ValueError("MCP arguments must be non-empty and bounded")
            if _contains_control_characters(value):
                raise ValueError("MCP arguments contain control characters")
            if _SECRET_ARGUMENT.search(value) or _SECRET_VALUE.search(value):
                raise ValueError("MCP arguments must not contain inline secrets")
        return values

    @field_validator("env")
    @classmethod
    def validate_environment_names(
        cls, values: tuple[str, ...]
    ) -> tuple[str, ...]:
        if any(not _ENVIRONMENT_NAME.fullmatch(value) for value in values):
            raise ValueError("MCP env accepts environment names only")
        return tuple(sorted(set(values)))


class McpConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_path: Path
    servers: dict[str, McpServerConfig]
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class McpConfigLoader:
    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()

    def load(self, config_path: str | Path) -> McpConfiguration:
        source_path = self._project_path(config_path, label="MCP config")
        try:
            raw = json.loads(source_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise McpConfigError(f"Unable to read MCP config: {source_path}") from exc
        except json.JSONDecodeError as exc:
            raise McpConfigError(
                f"MCP config is not valid JSON at line {exc.lineno}, column {exc.colno}"
            ) from exc
        if not isinstance(raw, dict):
            raise McpConfigError("MCP config root must be a JSON object")
        if set(raw) != {"mcpServers"}:
            raise McpConfigError("MCP config only accepts the mcpServers field")
        raw_servers = raw["mcpServers"]
        if not isinstance(raw_servers, dict):
            raise McpConfigError("mcpServers must be a JSON object")
        if len(raw_servers) > 100:
            raise McpConfigError("MCP config supports at most 100 servers")

        servers: dict[str, McpServerConfig] = {}
        for name, raw_server in raw_servers.items():
            if not isinstance(name, str) or not _SERVER_NAME.fullmatch(name):
                raise McpConfigError(f"Invalid MCP server name: {name!r}")
            servers[name] = self._parse_server(name, raw_server)

        canonical = {
            "mcpServers": {
                name: self._canonical_server(server)
                for name, server in sorted(servers.items())
            }
        }
        serialized = json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return McpConfiguration(
            source_path=source_path,
            servers=servers,
            config_hash=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        )

    def _parse_server(self, name: str, raw_server: Any) -> McpServerConfig:
        if not isinstance(raw_server, dict):
            raise McpConfigError(f"MCP server '{name}' must be a JSON object")
        if isinstance(raw_server.get("env"), dict):
            raise McpConfigError(
                "MCP env accepts environment names only; inline secret values are forbidden"
            )
        values = dict(raw_server)
        cwd = values.get("cwd")
        if cwd is not None:
            if not isinstance(cwd, str) or not cwd or _contains_control_characters(cwd):
                raise McpConfigError(f"MCP server '{name}' has an invalid cwd")
            values["cwd"] = self._project_path(cwd, label=f"MCP server '{name}' cwd")
        try:
            return McpServerConfig.model_validate(values)
        except ValidationError as exc:
            raise McpConfigError(f"Invalid MCP server '{name}': {exc}") from exc

    def _project_path(self, value: str | Path, *, label: str) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = self.project_root / path
        resolved = path.resolve()
        try:
            resolved.relative_to(self.project_root)
        except ValueError as exc:
            raise McpConfigError(f"{label} is outside project root") from exc
        return resolved

    def _canonical_server(self, server: McpServerConfig) -> dict[str, object]:
        result: dict[str, object] = {
            "command": server.command,
            "args": list(server.args),
        }
        if server.cwd is not None:
            result["cwd"] = server.cwd.relative_to(self.project_root).as_posix()
        if server.env:
            result["env"] = list(server.env)
        return result
