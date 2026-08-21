"""Time-bounded, operator-only gate for the frozen legacy baseline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field, model_validator


class LegacyMigrationSettings(BaseModel):
    enabled: bool = False
    enabled_at: datetime | None = None
    release_window_ends: tuple[datetime, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _enabled_requires_time(self):
        if self.enabled and self.enabled_at is None:
            raise ValueError("legacy enabled_at is required when enabled")
        return self


@dataclass(frozen=True, slots=True)
class LegacyMigrationDecision:
    allowed: bool
    code: str
    delete_deadline: datetime | None


class LegacyMigrationPolicy:
    """Never used by the normal Router path; only an explicit operator action."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        enabled_at: datetime | None = None,
        release_window_ends: tuple[datetime, ...] = (),
    ) -> None:
        self.settings = LegacyMigrationSettings(
            enabled=enabled,
            enabled_at=enabled_at,
            release_window_ends=release_window_ends,
        )

    def delete_deadline(self) -> datetime | None:
        enabled_at = self.settings.enabled_at
        if enabled_at is None:
            return None
        enabled_at = _utc(enabled_at)
        deadlines = [enabled_at + timedelta(days=14)]
        windows = sorted(_utc(value) for value in self.settings.release_window_ends)
        if len(windows) >= 2:
            deadlines.append(windows[1])
        return min(deadlines)

    def authorize(
        self, *, actor_subject: str, actor_role: str, reason: str, now: datetime
    ) -> LegacyMigrationDecision:
        del actor_subject
        deadline = self.delete_deadline()
        if not self.settings.enabled:
            return LegacyMigrationDecision(False, "legacy_path_disabled", deadline)
        if actor_role not in {"operator", "admin"}:
            return LegacyMigrationDecision(False, "legacy_path_operator_required", deadline)
        if not reason.strip():
            return LegacyMigrationDecision(False, "legacy_path_reason_required", deadline)
        if deadline is None or _utc(now) >= deadline:
            return LegacyMigrationDecision(False, "legacy_path_expired", deadline)
        return LegacyMigrationDecision(True, "legacy_path_allowed", deadline)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
