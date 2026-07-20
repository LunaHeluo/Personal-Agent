import pytest

from starter_agent.knowledge.errors import KnowledgeError
from starter_agent.knowledge.security import validate_markdown_upload


def test_markdown_upload_requires_authorization() -> None:
    with pytest.raises(KnowledgeError) as error:
        validate_markdown_upload(
            filename="resume.md",
            content=b"# Resume\nSafe content",
            confirmed_authorized=False,
            max_bytes=1024,
            allowed_extensions=[".md"],
        )

    assert error.value.code == "upload_authorization_required"


@pytest.mark.parametrize(
    ("filename", "content", "code"),
    [
        ("resume.txt", b"plain text", "unsupported_document_type"),
        ("resume.md", b"\x00binary", "document_invalid_encoding"),
        ("resume.md", b"\xff\xfe", "document_invalid_encoding"),
        (
            "resume.md",
            b"# Config\napi_key = sk-1234567890abcdef",
            "sensitive_content_detected",
        ),
    ],
)
def test_markdown_upload_rejects_unsafe_inputs(
    filename: str, content: bytes, code: str
) -> None:
    with pytest.raises(KnowledgeError) as error:
        validate_markdown_upload(
            filename=filename,
            content=content,
            confirmed_authorized=True,
            max_bytes=1024,
            allowed_extensions=[".md"],
        )

    assert error.value.code == code


def test_markdown_upload_returns_normalized_safe_content() -> None:
    result = validate_markdown_upload(
        filename="resume.md",
        content=b"\xef\xbb\xbf# Resume\r\nSafe content\r\n",
        confirmed_authorized=True,
        max_bytes=1024,
        allowed_extensions=[".md"],
    )

    assert result.filename == "resume.md"
    assert result.text == "# Resume\nSafe content\n"
    assert len(result.content_sha256) == 64
