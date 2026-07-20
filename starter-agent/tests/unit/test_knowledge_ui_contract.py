from pathlib import Path


HTML = Path("src/web/index.html").read_text(encoding="utf-8")


def test_primary_navigation_and_knowledge_controls_exist() -> None:
    for contract in (
        'id="chatNavButton"',
        'id="knowledgeNavButton"',
        ">知识库<",
        'id="knowledgeView"',
        'id="knowledgeFile"',
        'accept=".md,.markdown"',
        'id="knowledgeAuthorized"',
        'id="knowledgeUploadButton"',
        'id="knowledgeStatus"',
        'aria-live="polite"',
        'id="knowledgeDocumentList"',
        'id="knowledgeChunkPreview"',
        'id="chatKnowledgeMode"',
    ):
        assert contract in HTML


def test_knowledge_ui_calls_lifecycle_apis_and_uses_safe_rendering() -> None:
    for contract in (
        "/v1/knowledge-bases",
        "/documents",
        "/chunks",
        'method: "DELETE"',
        '"If-Match"',
        "window.confirm",
        "textContent",
        "上传失败",
        "解析或索引失败",
        "删除失败",
        'payload.knowledge_mode = "required"',
    ):
        assert contract in HTML
    assert "knowledgeDocumentList.innerHTML" not in HTML
    assert "knowledgeChunkPreview.innerHTML" not in HTML
