from pathlib import Path


def test_frontend_supports_manual_email_preview_approval_and_send() -> None:
    html = (
        Path(__file__).resolve().parents[2] / "src" / "web" / "index.html"
    ).read_text(encoding="utf-8")

    assert "queueEmailApproval" in html
    assert "renderEmailApprovalCard" in html
    assert "confirmAndSendEmail" in html
    assert "cancelEmailApproval" in html
    assert "/v1/email/drafts/${item.draftId}/approval-challenges" in html
    assert (
        "/v1/email/approval-challenges/"
        "${approval.approval_id}/confirm"
    ) in html
    assert "/v1/email/approvals/${approval.approval_id}/send" in html
    assert "邮件已成功发送" in html
    assert "发送结果待核验，请勿重复发送" in html
