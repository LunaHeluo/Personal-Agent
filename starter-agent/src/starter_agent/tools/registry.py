from __future__ import annotations

from starter_agent.domain.errors import ConfigurationError
from starter_agent.settings import (
    PROJECT_ROOT,
    AgentSettings,
    EmailProfileConfig,
    EmailToolConfig,
    JobDescriptionToolConfig,
)
from starter_agent.tools.base import Tool
from starter_agent.tools.adapters.job_description_extractor import (
    JobDescriptionExtractor,
)
from starter_agent.tools.adapters.safe_web_fetcher import SafeWebFetcher
from starter_agent.tools.builtin.job_description_search import (
    SearchJobDescriptionTool,
)
from starter_agent.tools.builtin.job_search import SearchJobsSerpApiTool
from starter_agent.tools.builtin.resume import (
    CompareResumeTool,
    CompareResumeToJdTool,
    DraftResumePatchTool,
    ListResumeVersionsTool,
    ReadResumeTool,
    ResumeManager,
    SaveResumeTool,
    SaveResumeVersionTool,
)
from starter_agent.tools.builtin.time_tool import GetCurrentTimeTool
from starter_agent.tools.email.manager import EmailManager
from starter_agent.tools.email.store import SQLiteEmailStore
from starter_agent.tools.email.tools import (
    EmailCreateDraftTool,
    EmailReadTool,
    EmailSearchTool,
    EmailSendTool,
)


class ToolRegistry:
    def __init__(self, enabled: list[str], settings: AgentSettings | None = None):
        project_root = settings.project_root if settings else PROJECT_ROOT
        resume_manager = ResumeManager(
            project_root,
            storage_root=(
                settings.resolve_path(settings.tools.resume.root)
                if settings
                else project_root
            ),
        )
        search_tool = SearchJobsSerpApiTool(
            key_resolver=(settings.serpapi_api_key if settings else None),
            timeout=(settings.tools.serpapi.timeout_seconds if settings else 15),
            max_retries=(settings.tools.serpapi.max_retries if settings else 1),
            retry_backoff_seconds=(
                settings.tools.serpapi.retry_backoff_seconds if settings else 0.5
            ),
        )
        job_config = (
            settings.tools.job_description
            if settings
            else JobDescriptionToolConfig()
        )
        job_description_tool = SearchJobDescriptionTool(
            fetcher=SafeWebFetcher.from_config(job_config),
            extractor=JobDescriptionExtractor(),
        )
        available: dict[str, Tool] = {
            GetCurrentTimeTool.name: GetCurrentTimeTool(),
            SearchJobsSerpApiTool.name: search_tool,
            SearchJobDescriptionTool.name: job_description_tool,
            ReadResumeTool.name: ReadResumeTool(resume_manager),
            ListResumeVersionsTool.name: ListResumeVersionsTool(resume_manager),
            SaveResumeTool.name: SaveResumeTool(resume_manager),
            CompareResumeTool.name: CompareResumeTool(resume_manager),
            CompareResumeToJdTool.name: CompareResumeToJdTool(resume_manager),
            DraftResumePatchTool.name: DraftResumePatchTool(resume_manager),
            SaveResumeVersionTool.name: SaveResumeVersionTool(resume_manager),
        }
        self.email_manager: EmailManager | None = None
        email_tool_names = {
            EmailSearchTool.name,
            EmailReadTool.name,
            EmailCreateDraftTool.name,
            EmailSendTool.name,
        }
        if email_tool_names.intersection(enabled):
            if settings:
                email_config = settings.tools.email
                database_url = settings.app.database_url
                environment_resolver = settings.email_environment_value
            else:
                email_config = EmailToolConfig(
                    active_profile="mock",
                    profiles={
                        "mock": EmailProfileConfig(
                            adapter="mock_fixture",
                            fixture_root="tests/fixtures/email",
                        )
                    },
                )
                database_url = "sqlite:///data/agent.db"
                environment_resolver = None
            self.email_manager = EmailManager(
                config=email_config,
                project_root=project_root,
                store=SQLiteEmailStore(database_url, project_root),
                environment_resolver=environment_resolver,
            )
            available.update(
                {
                    EmailSearchTool.name: EmailSearchTool(self.email_manager),
                    EmailReadTool.name: EmailReadTool(self.email_manager),
                    EmailCreateDraftTool.name: EmailCreateDraftTool(
                        self.email_manager
                    ),
                    EmailSendTool.name: EmailSendTool(self.email_manager),
                }
            )
        unknown = set(enabled) - set(available)
        if unknown:
            raise ConfigurationError(f"Unknown enabled tools: {sorted(unknown)}")
        self._tools = {name: available[name] for name in enabled}

    def list(self) -> list[Tool]:
        return list(self._tools.values())

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict]:
        return [tool.schema() for tool in self.list()]
