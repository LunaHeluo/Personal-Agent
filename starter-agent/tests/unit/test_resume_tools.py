import json
from uuid import uuid4

from starter_agent.tools.base import ToolContext
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


def context() -> ToolContext:
    return ToolContext(session_id=uuid4(), turn_id=uuid4())


def write_resume(tmp_path, name="resume.md", skill="Python"):
    path = tmp_path / name
    path.write_text(
        f"# Skills\n{skill}\n\n# Project\nBuilt a course RAG demo.\n",
        encoding="utf-8",
    )
    return path


async def test_read_resume_returns_sections_and_hash(tmp_path) -> None:
    path = write_resume(tmp_path)
    tool = ReadResumeTool(ResumeManager(tmp_path))

    result = await tool.execute({"path": path.name}, context())

    assert result.ok
    assert result.data["path"] == "resume.md"
    assert len(result.data["sha256"]) == 64
    assert [section["title"] for section in result.data["sections"]] == [
        "Skills",
        "Project",
    ]
    assert "Built a course RAG demo." in result.data["sections"][1]["content"]


async def test_read_resume_rejects_path_outside_project(tmp_path) -> None:
    tool = ReadResumeTool(ResumeManager(tmp_path))
    result = await tool.execute({"path": "../private-resume.md"}, context())
    assert result.error_code == "path_not_allowed"


async def test_save_initial_resume_and_list_it(tmp_path) -> None:
    manager = ResumeManager(tmp_path)
    save_tool = SaveResumeTool(manager)

    saved = await save_tool.execute(
        {
            "filename": "my_resume.md",
            "content": "# Skills\nPython and AI agents\n",
            "confirmed": True,
        },
        context(),
    )
    listed = await ListResumeVersionsTool(manager).execute({}, context())
    read = await ReadResumeTool(manager).execute(
        {"path": "my_resume.md"}, context()
    )

    assert saved.ok
    assert saved.data["path"] == "my_resume.md"
    assert (tmp_path / "my_resume.md").exists()
    assert listed.ok
    assert listed.data["root"] == tmp_path.as_posix()
    assert listed.data["files"][0]["path"] == "my_resume.md"
    assert read.ok
    assert read.data["sections"][0]["title"] == "Skills"


async def test_save_initial_resume_requires_confirmation_and_never_overwrites(
    tmp_path,
) -> None:
    manager = ResumeManager(tmp_path)
    tool = SaveResumeTool(manager)
    payload = {
        "filename": "resume.md",
        "content": "# Resume\nOriginal\n",
        "confirmed": False,
    }

    unconfirmed = await tool.execute(payload, context())
    payload["confirmed"] = True
    first = await tool.execute(payload, context())
    payload["content"] = "# Resume\nOverwrite\n"
    second = await tool.execute(payload, context())

    assert unconfirmed.error_code == "approval_required"
    assert first.ok
    assert second.error_code == "file_already_exists"
    assert (tmp_path / "resume.md").read_text(encoding="utf-8") == (
        "# Resume\nOriginal\n"
    )


async def test_configured_resume_root_is_the_only_allowed_directory(
    tmp_path,
) -> None:
    storage = tmp_path / "examples" / "job-hunt-agent" / "data"
    storage.mkdir(parents=True)
    (storage / "resume.md").write_text("# Resume\n", encoding="utf-8")
    manager = ResumeManager(tmp_path, storage_root=storage)

    inside = await ReadResumeTool(manager).execute(
        {"path": "resume.md"}, context()
    )
    outside = await ReadResumeTool(manager).execute(
        {"path": str(tmp_path / "outside.md")}, context()
    )

    assert inside.ok
    assert inside.data["path"] == "resume.md"
    assert outside.error_code == "path_not_allowed"


async def test_compare_resume_returns_diff_without_modifying_files(tmp_path) -> None:
    base = write_resume(tmp_path, "base.md", "Python")
    target = write_resume(tmp_path, "target.md", "Python, FastAPI")
    original = base.read_text(encoding="utf-8")
    tool = CompareResumeTool(ResumeManager(tmp_path))

    result = await tool.execute(
        {"base_path": base.name, "target_path": target.name}, context()
    )

    assert result.ok
    assert result.data["changed"] is True
    assert "+Python, FastAPI" in result.data["diff"]
    assert base.read_text(encoding="utf-8") == original


async def test_compare_resume_to_jd_returns_traceable_evidence_and_gaps(tmp_path) -> None:
    resume = tmp_path / "resume.md"
    resume.write_text(
        "# Profile\nIT master graduate\n\n"
        "# Project\nBuilt a RAG system with LangChain and a vector database.\n"
        "Benchmarked model output in controlled experiments.\n",
        encoding="utf-8",
    )
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    (jobs / "job_001_agent.json").write_text(
        json.dumps(
            {
                "id": "job_001",
                "title": "AI Agent Engineer",
                "company": "Example",
                "requirements": [
                    "本科及以上学历。",
                    "有 RAG 或向量数据库经验。",
                    "有 MCP 工程经验。",
                ],
                "nice_to_have": ["有评测体系经验。"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    original = resume.read_text(encoding="utf-8")
    tool = CompareResumeToJdTool(ResumeManager(tmp_path))

    result = await tool.execute(
        {"resume_path": "resume.md", "job_id": "job_001"}, context()
    )

    assert result.ok
    assert result.data["resume"]["path"] == "resume.md"
    assert result.data["job"]["id"] == "job_001"
    assert result.data["method"] == "deterministic_concept_evidence_v1"
    rag = next(
        item for item in result.data["comparisons"] if "RAG" in item["criterion"]
    )
    assert rag["status"] == "matched"
    assert rag["evidence"][0]["text"] == (
        "Built a RAG system with LangChain and a vector database."
    )
    mcp = next(
        item for item in result.data["comparisons"] if "MCP" in item["criterion"]
    )
    assert mcp["status"] == "gap"
    assert any("MCP" in item["criterion"] for item in result.data["gaps"])
    assert len(result.data["resume"]["sha256"]) == 64
    assert len(result.data["job"]["fingerprint"]) == 64
    assert resume.read_text(encoding="utf-8") == original


async def test_compare_resume_to_jd_accepts_user_provided_description(tmp_path) -> None:
    write_resume(tmp_path)
    tool = CompareResumeToJdTool(ResumeManager(tmp_path))

    result = await tool.execute(
        {
            "resume_path": "resume.md",
            "target_role": "AI Engineer",
            "job_description": "- 熟悉 RAG 或向量数据库\n- 有 MCP 经验",
        },
        context(),
    )

    assert result.ok
    assert result.data["job"]["source"]["type"] == "user_provided"
    assert result.data["job"]["title"] == "AI Engineer"
    assert result.data["summary"]["criteria_total"] == 2


async def test_compare_resume_to_jd_requires_exactly_one_job_source(tmp_path) -> None:
    write_resume(tmp_path)
    tool = CompareResumeToJdTool(ResumeManager(tmp_path))

    missing = await tool.execute({"resume_path": "resume.md"}, context())
    ambiguous = await tool.execute(
        {
            "resume_path": "resume.md",
            "job_id": "job_001",
            "job_description": "RAG engineer",
        },
        context(),
    )

    assert missing.error_code == "missing_job_description"
    assert ambiguous.error_code == "ambiguous_job_source"


async def test_compare_resume_to_jd_rejects_a_broad_role_as_the_jd(tmp_path) -> None:
    write_resume(tmp_path)
    tool = CompareResumeToJdTool(ResumeManager(tmp_path))

    result = await tool.execute(
        {"resume_path": "resume.md", "job_description": "上海 AI 岗位"},
        context(),
    )

    assert result.error_code == "invalid_job_description"


async def test_compare_resume_to_jd_reports_unknown_job_id(tmp_path) -> None:
    write_resume(tmp_path)
    (tmp_path / "jobs").mkdir()
    tool = CompareResumeToJdTool(ResumeManager(tmp_path))

    result = await tool.execute(
        {"resume_path": "resume.md", "job_id": "missing_job"}, context()
    )

    assert result.error_code == "job_not_found"


async def test_compare_resume_to_jd_does_not_use_ascii_substring_false_matches(
    tmp_path,
) -> None:
    (tmp_path / "resume.md").write_text(
        "# Experience\nRapidly iterated on product direction.\n",
        encoding="utf-8",
    )
    tool = CompareResumeToJdTool(ResumeManager(tmp_path))

    result = await tool.execute(
        {
            "resume_path": "resume.md",
            "job_description": "具备后端 API 开发经验",
        },
        context(),
    )

    assert result.ok
    assert result.data["comparisons"][0]["status"] == "gap"
    assert result.data["comparisons"][0]["evidence"] == []


async def test_draft_requires_evidence_from_original_resume(tmp_path) -> None:
    write_resume(tmp_path)
    tool = DraftResumePatchTool(ResumeManager(tmp_path))

    result = await tool.execute(
        {
            "resume_path": "resume.md",
            "target_text": "Built a course RAG demo.",
            "replacement_text": "Led a production platform with millions of users.",
            "evidence": ["millions of real users"],
        },
        context(),
    )

    assert not result.ok
    assert result.error_code == "missing_evidence"


async def test_draft_does_not_overwrite_source(tmp_path) -> None:
    path = write_resume(tmp_path)
    original = path.read_text(encoding="utf-8")
    tool = DraftResumePatchTool(ResumeManager(tmp_path))

    result = await tool.execute(
        {
            "resume_path": "resume.md",
            "target_text": "Built a course RAG demo.",
            "replacement_text": "Built and evaluated a course RAG demo.",
            "evidence": ["Built a course RAG demo."],
        },
        context(),
    )

    assert result.ok
    assert result.data["requires_approval"] is True
    assert "+Built and evaluated a course RAG demo." in result.data["diff"]
    assert path.read_text(encoding="utf-8") == original


async def test_save_requires_confirmation(tmp_path) -> None:
    write_resume(tmp_path)
    manager = ResumeManager(tmp_path)
    draft = manager.create_draft(
        "resume.md",
        "Built a course RAG demo.",
        "Built and evaluated a course RAG demo.",
        ["Built a course RAG demo."],
    )
    tool = SaveResumeVersionTool(manager)

    result = await tool.execute(
        {
            "draft_id": draft["draft_id"],
            "base_sha256": draft["base_sha256"],
            "confirmed": False,
        },
        context(),
    )

    assert result.error_code == "approval_required"
    assert not manager.manifest_path.exists()


async def test_save_creates_immutable_version_and_metadata(tmp_path) -> None:
    source = write_resume(tmp_path)
    original = source.read_text(encoding="utf-8")
    manager = ResumeManager(tmp_path)
    draft = manager.create_draft(
        "resume.md",
        "Built a course RAG demo.",
        "Built and evaluated a course RAG demo.",
        ["Built a course RAG demo."],
    )
    tool = SaveResumeVersionTool(manager)

    result = await tool.execute(
        {
            "draft_id": draft["draft_id"],
            "base_sha256": draft["base_sha256"],
            "confirmed": True,
            "label": "ai-agent",
        },
        context(),
    )

    assert result.ok
    assert result.data["version_number"] == 1
    version_path = tmp_path / result.data["version_path"]
    assert version_path.exists()
    assert "Built and evaluated" in version_path.read_text(encoding="utf-8")
    assert source.read_text(encoding="utf-8") == original
    versions = json.loads(manager.manifest_path.read_text(encoding="utf-8"))
    assert versions[0]["version_id"] == result.data["version_id"]
    assert versions[0]["sha256"] == result.data["sha256"]
    assert versions[0]["version_path"] == result.data["version_path"]

    reread = await ReadResumeTool(manager).execute(
        {"path": result.data["version_path"]}, context()
    )
    assert reread.ok
    assert reread.data["path"] == result.data["version_path"]
    assert reread.data["sha256"] == result.data["sha256"]
    assert any(
        "Built and evaluated" in section["content"]
        for section in reread.data["sections"]
    )


async def test_save_rejects_changed_parent_resume(tmp_path) -> None:
    source = write_resume(tmp_path)
    manager = ResumeManager(tmp_path)
    draft = manager.create_draft(
        "resume.md",
        "Built a course RAG demo.",
        "Built and evaluated a course RAG demo.",
        ["Built a course RAG demo."],
    )
    source.write_text(source.read_text(encoding="utf-8") + "Changed\n", encoding="utf-8")

    result = await SaveResumeVersionTool(manager).execute(
        {
            "draft_id": draft["draft_id"],
            "base_sha256": draft["base_sha256"],
            "confirmed": True,
        },
        context(),
    )

    assert result.error_code == "resume_version_conflict"
