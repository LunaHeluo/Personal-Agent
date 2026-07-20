from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from starter_agent.domain.errors import ConfigurationError


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class AppConfig(BaseModel):
    name: str = "Starter Agent"
    environment: str = "development"
    database_url: str = "sqlite:///data/agent.db"
    log_path: str = "logs/agent.jsonl"
    identity_path: str = "docs/agent.md"


class ModelConfig(BaseModel):
    default_provider: str = "mock"
    default_model: str = "starter-mock"
    temperature: float = 0.2
    timeout_seconds: float = 60
    max_retries: int = 2


class ProviderConfig(BaseModel):
    type: Literal["mock", "openai_compatible"]
    models: list[str] = Field(default_factory=list)
    base_url: str | None = None
    api_key_env: str | None = None
    stream: bool = False
    thinking: Literal["enabled", "disabled"] | None = None


class RuntimeConfig(BaseModel):
    max_model_calls: int = Field(default=4, ge=1, le=20)
    max_tool_calls: int = Field(default=4, ge=0, le=20)
    max_seconds: float = Field(default=90, gt=0)
    tool_timeout_seconds: float = Field(default=35, gt=0)
    max_tool_result_chars: int = Field(default=8000, ge=100)


class ContextConfig(BaseModel):
    max_total_tokens: int = Field(default=128_000, ge=1)
    warning_ratio: float = Field(default=0.8, gt=0, le=1)
    compact_trigger_ratio: float = Field(default=0.75, gt=0, le=1)
    hard_prompt_ratio: float = Field(default=0.85, gt=0, le=1)
    history_budget_tokens: int = Field(default=6000, ge=100)
    keep_recent_turns: int = Field(default=6, ge=1, le=50)
    per_tool_result_tokens: int = Field(default=4000, ge=100)
    all_tool_results_tokens: int = Field(default=16000, ge=100)
    estimator_safety_ratio: float = Field(default=1.15, ge=1, le=2)


class MemoryConfig(BaseModel):
    auto_write_enabled: bool = True
    min_confidence: float = Field(default=0.85, ge=0.5, le=1)
    max_candidates_per_turn: int = Field(default=5, ge=1, le=10)
    source_max_chars: int = Field(default=20_000, ge=1000, le=50_000)
    timeout_seconds: float = Field(default=30, gt=0, le=120)


class QueryMappingConfig(BaseModel):
    version: str = Field(
        default="builtin-v1",
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
    )
    groups: dict[str, list[str]] = Field(default_factory=dict)
    disabled_groups: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_mappings(self) -> "QueryMappingConfig":
        if (self.groups or self.disabled_groups) and "version" not in self.model_fields_set:
            raise ValueError("query mapping overrides require an explicit version")
        if len(self.groups) > 100:
            raise ValueError("query mappings support at most 100 groups")
        seen: dict[str, str] = {}
        reserved = {"and", "or", "not", "near"}
        normalized_groups: dict[str, list[str]] = {}
        for group_id, values in self.groups.items():
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", group_id):
                raise ValueError("query mapping group ids must be lowercase slugs")
            if not 2 <= len(values) <= 32:
                raise ValueError("query mapping groups require 2 to 32 terms")
            normalized: list[str] = []
            for raw in values:
                value = " ".join(raw.split()).strip()
                folded = value.casefold()
                if (
                    not value
                    or len(value) > 64
                    or "\x00" in value
                    or any(ord(char) < 32 for char in value)
                    or folded in reserved
                ):
                    raise ValueError("invalid query mapping term")
                if folded in seen and seen[folded] != group_id:
                    raise ValueError("query mapping terms cannot span groups")
                seen[folded] = group_id
                if folded not in {item.casefold() for item in normalized}:
                    normalized.append(value)
            normalized_groups[group_id] = normalized
        if len(seen) > 500:
            raise ValueError("query mappings support at most 500 terms")
        self.groups = normalized_groups
        self.disabled_groups = list(dict.fromkeys(self.disabled_groups))
        return self


class KnowledgeConfig(BaseModel):
    enabled: bool = True
    default_user_id: str = Field(default="local-user", min_length=1, max_length=120)
    default_project_id: str = Field(
        default="default-project", min_length=1, max_length=120
    )
    max_upload_bytes: int = Field(default=2 * 1024 * 1024, ge=1, le=20 * 1024 * 1024)
    max_documents: int = Field(default=100, ge=1, le=10_000)
    max_chunks: int = Field(default=5_000, ge=1, le=100_000)
    allowed_extensions: list[str] = Field(
        default_factory=lambda: [".md", ".markdown"]
    )
    chunk_target_chars: int = Field(default=1200, ge=100, le=20_000)
    chunk_overlap_chars: int = Field(default=150, ge=0, le=5_000)
    retrieval_top_k: int = Field(default=6, ge=1, le=50)
    query_mappings: QueryMappingConfig = Field(default_factory=QueryMappingConfig)

    @model_validator(mode="after")
    def validate_chunk_overlap(self) -> "KnowledgeConfig":
        if self.chunk_overlap_chars >= self.chunk_target_chars:
            raise ValueError("knowledge chunk overlap must be smaller than target")
        normalized = [value.lower() for value in self.allowed_extensions]
        if not normalized or any(not value.startswith(".") for value in normalized):
            raise ValueError("knowledge allowed_extensions must contain suffixes")
        self.allowed_extensions = normalized
        return self


class SerpApiKeyConfig(BaseModel):
    api_key_env: str


class SerpApiToolConfig(BaseModel):
    active_key: str = "primary"
    active_key_env: str = "SERPAPI_ACTIVE_KEY"
    timeout_seconds: float = Field(default=15, gt=0, le=60)
    max_retries: int = Field(default=1, ge=0, le=3)
    retry_backoff_seconds: float = Field(default=0.5, ge=0, le=5)
    keys: dict[str, SerpApiKeyConfig] = Field(default_factory=dict)


class ResumeToolConfig(BaseModel):
    root: str = "examples/job-hunt-agent/job-hunt-agent/data"


class EmailAuthConfig(BaseModel):
    type: Literal["oauth", "app_password", "qq_auth_code"]
    credential_env: str | None = None
    oauth_client_id_env: str | None = None
    oauth_refresh_token_env: str | None = None

    @model_validator(mode="after")
    def validate_environment_references(self) -> "EmailAuthConfig":
        if self.type == "oauth":
            if not self.oauth_client_id_env or not self.oauth_refresh_token_env:
                raise ValueError(
                    "OAuth email auth requires oauth_client_id_env and "
                    "oauth_refresh_token_env"
                )
        elif not self.credential_env:
            raise ValueError(
                "Email password/auth-code configuration requires credential_env"
            )
        return self


class EmailConnectionConfig(BaseModel):
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65_535)
    transport: Literal["ssl_tls", "starttls"]


class EmailProfileConfig(BaseModel):
    adapter: Literal["mock_fixture", "imap_smtp"]
    enabled: bool = True
    mailbox_type: Literal["gmail", "qq", "custom"] | None = None
    account_env: str | None = None
    auth: EmailAuthConfig | None = None
    imap: EmailConnectionConfig | None = None
    smtp: EmailConnectionConfig | None = None
    drafts_mailbox: str | None = None
    fixture_root: str | None = None
    real_send_enabled: bool = False

    @model_validator(mode="after")
    def validate_adapter_fields(self) -> "EmailProfileConfig":
        if self.adapter == "mock_fixture":
            if not self.fixture_root:
                raise ValueError("Mock email profile requires fixture_root")
            if self.real_send_enabled:
                raise ValueError("Mock email profile cannot enable real sending")
            return self
        missing: list[str] = []
        if not self.mailbox_type:
            missing.append("mailbox_type")
        if not self.account_env:
            missing.append("account_env")
        if not self.auth:
            missing.append("auth")
        if not self.imap:
            missing.append("imap")
        if not self.smtp:
            missing.append("smtp")
        if missing:
            raise ValueError(
                "IMAP/SMTP email profile is missing: " + ", ".join(missing)
            )
        return self


class EmailToolConfig(BaseModel):
    active_profile: str = "mock"
    result_max_items: int = Field(default=20, ge=1, le=100)
    body_max_chars: int = Field(default=12_000, ge=1_000, le=50_000)
    approval_ttl_seconds: int = Field(default=600, ge=60, le=3_600)
    attachment_root: str = "data/email_attachments"
    profiles: dict[str, EmailProfileConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_active_profile(self) -> "EmailToolConfig":
        if self.profiles and self.active_profile not in self.profiles:
            raise ValueError(
                f"Unknown active email profile: {self.active_profile}"
            )
        return self


class JobDescriptionToolConfig(BaseModel):
    fetch_timeout_seconds: float = Field(default=10, gt=0, le=30)
    max_response_bytes: int = Field(
        default=1_000_000,
        ge=10_000,
        le=5_000_000,
    )
    max_redirects: int = Field(default=3, ge=0, le=5)
    user_agent: str = Field(
        default="StarterAgentJobDescription/0.1",
        min_length=1,
        max_length=200,
    )
    respect_robots: bool = True

    @field_validator("user_agent", mode="before")
    @classmethod
    def normalize_user_agent(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("User agent must not be blank")
        has_non_printable_or_non_ascii = any(
            not 32 <= ord(character) <= 126
            for character in normalized
        )
        if has_non_printable_or_non_ascii:
            raise ValueError("User agent must contain printable ASCII characters only")
        return normalized


class ToolsConfig(BaseModel):
    enabled: list[str] = Field(default_factory=lambda: ["get_current_time"])
    allow_risk_levels: list[str] = Field(default_factory=lambda: ["read"])
    serpapi: SerpApiToolConfig = Field(default_factory=SerpApiToolConfig)
    resume: ResumeToolConfig = Field(default_factory=ResumeToolConfig)
    email: EmailToolConfig = Field(default_factory=EmailToolConfig)
    job_description: JobDescriptionToolConfig = Field(
        default_factory=JobDescriptionToolConfig
    )


class AgentSettings(BaseModel):
    app: AppConfig = Field(default_factory=AppConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    providers: dict[str, ProviderConfig]
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    project_root: Path = PROJECT_ROOT

    @model_validator(mode="after")
    def validate_email_fixture_paths(self) -> "AgentSettings":
        root = self.project_root.resolve()
        for name, profile in self.tools.email.profiles.items():
            if profile.adapter != "mock_fixture" or not profile.fixture_root:
                continue
            path = self.resolve_path(profile.fixture_root).resolve()
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise ValueError(
                    f"Email fixture path for profile '{name}' is outside project root"
                ) from exc
        return self

    def resolve_path(self, value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else self.project_root / path

    def provider_api_key(self, provider_name: str) -> str | None:
        provider = self.providers[provider_name]
        if not provider.api_key_env:
            return None
        return self._environment_value(provider.api_key_env)

    def serpapi_api_key(self) -> tuple[str, str | None, str | None]:
        config = self.tools.serpapi
        profile = self._environment_value(config.active_key_env) or config.active_key
        selected = config.keys.get(profile)
        if selected is None:
            return profile, None, None
        return (
            profile,
            self._environment_value(selected.api_key_env),
            selected.api_key_env,
        )

    def email_environment_value(self, name: str) -> str | None:
        return self._environment_value(name)

    def _environment_value(self, name: str) -> str | None:
        current = os.getenv(name)
        if current:
            return current
        env_path = self.project_root / ".env"
        if not env_path.exists():
            return None
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == name:
                return value.strip().strip("\"'")
        return None


class EnvironmentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    starter_agent_config: str | None = None


def load_settings(config_path: str | Path | None = None) -> AgentSettings:
    env = EnvironmentSettings()
    selected = config_path or env.starter_agent_config or "config/config.yaml"
    path = Path(selected)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if not path.exists():
        example = PROJECT_ROOT / "config/config.example.yaml"
        path = example if example.exists() else path
    if not path.exists():
        raise ConfigurationError(f"Config file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    settings = AgentSettings.model_validate(raw)
    if settings.model.default_provider not in settings.providers:
        raise ConfigurationError(
            f"Unknown default provider: {settings.model.default_provider}"
        )
    default_models = settings.providers[settings.model.default_provider].models
    if default_models and settings.model.default_model not in default_models:
        raise ConfigurationError(
            "Default model is not listed for the default provider",
            suggestion=(
                "请将 model.default_model 设置为 default_provider 的 models 列表中的模型"
            ),
        )
    return settings
