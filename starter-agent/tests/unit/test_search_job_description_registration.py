from starter_agent.settings import JobDescriptionToolConfig, load_settings


def test_job_description_config_has_safe_defaults() -> None:
    config = JobDescriptionToolConfig()

    assert config.fetch_timeout_seconds == 10
    assert config.max_response_bytes == 1_000_000
    assert config.max_redirects == 3
    assert config.user_agent == "StarterAgentJobDescription/0.1"
    assert config.respect_robots is True


def test_runtime_config_enables_job_description_tool() -> None:
    settings = load_settings("config/config.yaml")

    assert "search_job_description" in settings.tools.enabled
    assert settings.tools.job_description.max_response_bytes == 1_000_000
