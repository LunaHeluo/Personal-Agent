from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field


class RuntimeRevision(BaseModel):
    """Immutable identity for one loaded runtime configuration."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=64, max_length=64)
    code_version: str = Field(min_length=1, max_length=160)
    skill_revision: int = Field(ge=0)
    tool_revision: str = Field(min_length=1, max_length=160)
    prompt_hash: str = Field(min_length=64, max_length=64)
    config_hash: str = Field(min_length=64, max_length=64)

    @classmethod
    def build(
        cls,
        *,
        code_version: str,
        skill_revision: int,
        tool_revision: str,
        prompt_hash: str,
        config_hash: str,
    ) -> "RuntimeRevision":
        payload = {
            "code_version": code_version,
            "skill_revision": skill_revision,
            "tool_revision": tool_revision,
            "prompt_hash": prompt_hash,
            "config_hash": config_hash,
        }
        revision_id = hashlib.sha256(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return cls(id=revision_id, **payload)

    def requires_restart(self, desired: "RuntimeRevision") -> bool:
        return self.id != desired.id
