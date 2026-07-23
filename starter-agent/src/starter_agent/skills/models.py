from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SkillDependency(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["tool", "mcp", "service"]
    name: str = Field(min_length=1, max_length=200)
    required: bool = True

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.name}"


class SkillDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,99}$")
    description: str = Field(min_length=1, max_length=2_000)
    version: str = Field(min_length=1, max_length=100)
    source: str = Field(min_length=1, max_length=200)
    source_path: str = Field(min_length=1, max_length=1_000)
    enabled: bool = False
    dependencies: tuple[SkillDependency, ...] = ()
    trigger_examples: tuple[str, ...] = Field(min_length=1, max_length=100)
    negative_examples: tuple[str, ...] = Field(min_length=1, max_length=100)
    validation: tuple[str, ...] = Field(min_length=1, max_length=100)
    failure_policy: tuple[str, ...] = Field(min_length=1, max_length=100)
    definition: str = Field(min_length=1, max_length=200_000)
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    dependency_state: Literal["available", "dependency_unavailable"] = "available"
    missing_dependencies: tuple[str, ...] = ()


class SkillSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    revision: int = Field(ge=0)
    skills: tuple[SkillDefinition, ...] = ()
    loaded_at: datetime
    stale: bool = False
    last_error: str | None = Field(default=None, max_length=2_000)


class SkillToolTrace(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool_name: str
    call_id: str
    arguments: dict[str, Any]
    result: dict[str, Any] | None = None
    gate_outcome: str
    error_code: str | None = None


class SkillRunResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    data: dict[str, Any] = Field(default_factory=dict)
    trace: tuple[SkillToolTrace, ...] = ()
    error_code: str | None = None
    missing_dependencies: tuple[str, ...] = ()
