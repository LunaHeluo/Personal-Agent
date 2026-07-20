from __future__ import annotations

from starter_agent.domain.errors import AgentError


_MESSAGES: dict[str, tuple[str, str, int]] = {
    "upload_authorization_required": (
        "上传前需要确认资料属于本人或已获授权",
        "请确认资料权限后重新上传",
        400,
    ),
    "unsupported_document_type": (
        "第一阶段只支持 Markdown 文档",
        "请选择 .md 或 .markdown 文件",
        415,
    ),
    "document_too_large": (
        "文档超过允许的大小",
        "请缩小文档后重新上传",
        413,
    ),
    "document_invalid_encoding": (
        "文档不是有效的 UTF-8 Markdown",
        "请将文件转换为 UTF-8 后重新上传",
        422,
    ),
    "sensitive_content_detected": (
        "文档包含禁止入库的敏感内容模式",
        "请移除凭据、密码、授权码或证件号码后重试",
        422,
    ),
    "duplicate_document_content": (
        "相同内容已经存在于当前知识库",
        "请使用已有文档，或通过更新操作提交新版本",
        409,
    ),
    "document_not_found": (
        "文档不存在或不可访问",
        "请刷新知识库列表后重试",
        404,
    ),
    "knowledge_base_not_found": (
        "知识库不存在或不可访问",
        "请刷新知识库列表后重试",
        404,
    ),
    "knowledge_capacity_exceeded": (
        "知识库文档数量已达到上限",
        "请删除不再需要的文档后重试",
        409,
    ),
    "document_no_indexable_content": (
        "文档没有可建立索引的正文内容",
        "请补充正文段落、列表、表格或代码内容后重试",
        422,
    ),
    "document_ingestion_failed": (
        "文档解析或切分失败",
        "请检查 Markdown 结构后重试",
        422,
    ),
    "fts5_unavailable": (
        "当前 SQLite 不支持 FTS5 全文检索",
        "请使用启用了 FTS5 的 SQLite 版本",
        503,
    ),
    "fts5_trigram_unavailable": (
        "当前 SQLite 不支持 FTS5 trigram 分词",
        "请升级 SQLite 后重试",
        503,
    ),
    "knowledge_query_invalid": (
        "知识库问题不能为空",
        "请输入需要从资料中查找的问题",
        400,
    ),
    "document_version_conflict": (
        "文档已被其他更新修改",
        "请刷新文档并基于最新版本重试",
        409,
    ),
    "citation_gone": (
        "该引用对应的文档版本已失效",
        "请重新检索并使用当前版本引用",
        410,
    ),
}


class KnowledgeError(AgentError):
    def __init__(
        self,
        code: str,
        *,
        rule_id: str | None = None,
        retryable: bool = False,
    ) -> None:
        message, suggestion, status = _MESSAGES.get(
            code,
            ("知识库请求处理失败", "请稍后重试", 400),
        )
        super().__init__(message, suggestion=suggestion)
        self.code = code
        self.http_status = status
        self.retryable = retryable
        self.rule_id = rule_id

    def to_public_dict(self) -> dict[str, object]:
        payload = super().to_public_dict()
        if self.rule_id:
            payload["rule_id"] = self.rule_id
        return payload
