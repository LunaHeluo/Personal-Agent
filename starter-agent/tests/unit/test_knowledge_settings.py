from starter_agent.settings import load_settings


def test_knowledge_defaults_are_safe_and_local() -> None:
    settings = load_settings("config/config.example.yaml")

    assert settings.knowledge.enabled is True
    assert settings.knowledge.default_user_id == "local-user"
    assert settings.knowledge.default_project_id == "default-project"
    assert settings.knowledge.max_upload_bytes == 2 * 1024 * 1024
    assert settings.knowledge.max_documents == 100
    assert settings.knowledge.max_chunks == 5000
    assert settings.knowledge.allowed_extensions == [".md", ".markdown"]
    assert settings.knowledge.chunk_target_chars == 1200
    assert settings.knowledge.chunk_overlap_chars == 150
    assert settings.knowledge.retrieval_top_k == 6
    assert settings.knowledge.query_mappings.version == "builtin-v1"
