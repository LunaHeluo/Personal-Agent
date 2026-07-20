# Starter Agent 个人知识库 / RAG 验收

## 环境准备

```powershell
uv sync --extra dev
uv run agent serve
```

在浏览器打开前端地址，确认侧边栏一级导航同时显示“对话”和“知识库”。
知识库后端要求 SQLite 支持 FTS5 与 `trigram` tokenizer；启动时能力检查失败必须
明确报错，不允许静默退化为全库扫描。

## 固定安全测试数据

- `tests/fixtures/knowledge/resume_demo.md`
- `tests/fixtures/knowledge/job_demo.md`

两份资料只包含虚构姓名、虚构组织及 `.test` 域名。验收时禁止替换为身份证、
未授权公司资料、真实 API Key、邮箱密码、授权码或真实私人文档。

## 自动化测试

Retrieval 独立验证，不调用生成模型：

```powershell
uv run pytest tests/unit/test_knowledge_query.py tests/unit/test_fts_index.py tests/integration/test_knowledge_retrieval_api.py -q
```

Generation 独立验证，使用固定 Evidence 和 Stub Provider，不调用 FTS：

```powershell
uv run pytest tests/unit/test_rag_context.py tests/unit/test_rag_citations.py tests/unit/test_rag_generation.py tests/unit/test_rag_refusal.py -q
```

生命周期和端到端验证：

```powershell
uv run pytest tests/integration/test_knowledge_update_delete.py tests/integration/test_rag_end_to_end.py -q
uv run pytest -q
```

可观察证据必须包括：

- 两份文档均为 `indexed`，文档列表的 `chunk_count` 大于零。
- Chunk 预览的标题路径、原始行号和片段与 fixture 一致。
- `Aurora 招聘知识库` 查询命中简历项目经历，并返回版本、Chunk ID 和行号。
- 带证据回答的 quote 是对应 Chunk 正文的连续子串。
- 不存在的联系方式返回 `refused` 和机器可判定的 `refusal_reason`。
- 更新后旧 Chunk 检索不到，旧引用返回 `410 citation_gone`。
- 硬删除后用原问题、唯一关键词、旧 document ID、旧 chunk ID 再验证；重建服务后
  旧内容仍不可检索。

## 前端人工验收

1. 使用键盘 Tab 访问一级“知识库”入口并按 Enter 打开。
2. 选择一份固定 Markdown，选择资料类型，勾选授权确认，点击“上传并入库”。
3. 观察上传、解析/索引成功状态；分别用错误扩展名和无授权确认验证错误提示。
4. 刷新页面，确认文档列表、版本、状态与 Chunk 数恢复显示。
5. 打开 Chunk 预览，核对章节、行号和受限正文片段。
6. 更新文档，确认请求携带当前内容指纹；用旧指纹验证明确的版本冲突。
7. 删除前确认对话框显示文件名和版本；取消时不删除，确认后列表才移除。
8. 返回“对话”，开启“知识库问答”，验证有证据回答带引用、无证据问题明确拒答；
   关闭后原有聊天和工具行为不变。

## 判定原则

模型输出流畅、模型自称“已检索”，或回答看起来合理，都不能单独作为通过依据。
验收只依据可重复的 Retrieval 命中、canonical 元数据、quote 连续子串、明确拒答、
更新/删除后的反向检索，以及重启后的持久化结果。
