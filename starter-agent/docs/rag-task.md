# Starter Agent 带引用的个人知识库 / RAG 实施任务

> 本文只定义实施拆分。实际执行进度由任务管理机制记录，不在本文维护。

本次 RAG 功能固定采用以下流程：

```text
rag-requirements.md
→ rag-design.md
→ rag-task.md
→ 按 Task 顺序执行
→ 最终验证
```

本文件位于设计确认之后、代码执行之前。只有计划确认后才能开始 Task1；Task9 通过后才能进入最终验证。

**目标：** 为 Starter Agent 实现可上传、可检索、带可定位引用、可更新、可硬删除且无证据时拒答的个人知识库。

**实施基线：**

- 第一阶段只接收 UTF-8 Markdown，不支持 PDF、DOCX、图片或压缩包。
- 检索采用 SQLite FTS5 `trigram` 全文索引、BM25 排序、元数据过滤和确定性字段加权。
- 第一阶段不引入 Embedding、向量索引、向量数据库、混合检索或模型 Rerank。
- Generation 只能使用本次 Retrieval 返回的证据；引用由服务端根据 canonical Chunk 元数据组装。
- 文档正文、Chunk、问题和引用片段不得进入普通日志。
- 所有数据访问强制使用 `user_id + project_id + knowledge_base_id` 过滤。
- 更新通过新版本建好后原子切换；删除必须清除正文、Chunk、FTS 条目和关联元数据。
- 测试遵循先失败、再最小实现、再回归验证的顺序。
- 每完成一个 Task，执行者必须汇报修改文件、测试命令与结果、验收结论和剩余风险。

## Task1：建立知识库领域模型、配置、存储和上传 API

### 任务目标

建立知识库最小后端闭环：用户能够通过 API 上传一份经过授权确认的 Markdown，系统完成文件校验、正文读取和文档元数据持久化，并能在刷新或重启后查询入库任务与文档记录。

### 子任务

1. 在 `pyproject.toml` 增加 FastAPI Multipart 上传所需的 `python-multipart` 依赖。
2. 在 `src/starter_agent/settings.py` 增加 `KnowledgeConfig`，至少包含：
   - `enabled`
   - `default_user_id`
   - `default_project_id`
   - `max_upload_bytes`
   - `max_documents`
   - `allowed_extensions`
   - `chunk_target_chars`
   - `chunk_overlap_chars`
   - `retrieval_top_k`
3. 在 `config/config.example.yaml` 增加不含秘密的 `knowledge` 示例配置，默认允许 `.md` 和 `.markdown`，单文件上限为 2 MiB。
4. 新建 `src/starter_agent/knowledge/models.py`，定义：
   - `KnowledgeScope`
   - `KnowledgeBase`
   - `KnowledgeDocument`
   - `DocumentVersion`
   - `IngestionJob`
   - 文档与任务的业务状态枚举
5. 新建 `src/starter_agent/knowledge/errors.py`，定义稳定错误码及安全公开消息，至少覆盖：
   - `upload_authorization_required`
   - `unsupported_document_type`
   - `document_too_large`
   - `document_invalid_encoding`
   - `sensitive_content_detected`
   - `duplicate_document_content`
   - `document_not_found`
6. 新建 `src/starter_agent/knowledge/security.py`：
   - 校验安全文件名和扩展名。
   - 严格 UTF-8 解码。
   - 拒绝 NUL、明显二进制内容和异常控制字符。
   - 扫描模拟 API Key、Token、密码、邮箱授权码和身份证件号码模式。
   - 只返回规则 ID，不返回命中原文。
7. 新建 `src/starter_agent/knowledge/store.py`，使用 SQLAlchemy/SQLite 建立：
   - `knowledge_bases`
   - `knowledge_documents`
   - `knowledge_document_versions`
   - `knowledge_ingestion_jobs`
8. Store 的所有读写方法必须接收 `KnowledgeScope`，不得提供绕过 Scope 的按 ID 查询。
9. 新建 `src/starter_agent/knowledge/service.py`，实现上传用例：
   - 要求 `confirmed_authorized=true`。
   - 读取受限字节。
   - 校验 Markdown。
   - 计算 SHA-256。
   - 保存文档、初始版本和入库任务。
   - 相同知识库内重复内容返回冲突，不创建重复记录。
10. 扩展 `src/starter_agent/bootstrap.py`，构造共享的 `KnowledgeApplicationService`。
11. 扩展 `src/starter_agent/interfaces/api.py`，增加：
    - `POST /v1/knowledge-bases/{knowledge_base_id}/documents`
    - `GET /v1/knowledge-bases/{knowledge_base_id}/documents`
    - `GET /v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}`
    - `GET /v1/knowledge-bases/{knowledge_base_id}/ingestion-jobs/{job_id}`
12. 增加单元与 API 测试，使用 `tmp_path` 和独立 SQLite 数据库，不读取真实私人文档。

### 依赖关系

无前置 Task。依赖现有 `AgentSettings`、FastAPI、SQLAlchemy、`bootstrap.py` 和 pytest fixture 模式。

### 验收标准

- 上传合法、无敏感信息的 Markdown 返回 `202`，响应包含 `document_id`、`version_id`、`job_id`、内容指纹和业务状态。
- 文档列表和任务查询在新建客户端或重建 Store 后仍能读取记录。
- 未确认授权、非 Markdown、超限文件、二进制内容、非 UTF-8 和模拟秘密均被明确拒绝。
- 相同 Scope 内重复内容不会创建第二份不可区分的记录。
- 使用另一 `user_id/project_id` 查询相同 ID 时不可见，且响应不泄露对象是否属于其他用户。
- 普通日志不包含上传正文、模拟秘密和完整本地路径。
- 通过以下测试：

```powershell
uv run pytest tests/unit/test_knowledge_settings.py tests/unit/test_knowledge_security.py tests/unit/test_knowledge_store.py tests/integration/test_knowledge_api.py -q
```

### 预估复杂度

高。涉及配置、领域模型、SQLite 表、Multipart API、安全校验和 Scope 隔离，是后续任务的数据基础。

## Task2：实现 Markdown 解析、Normalize、Chunk 和可定位元数据

### 任务目标

把已上传 Markdown 确定性地转换成可检索 Chunk，并保留文档版本、标题路径、原始行号和内容指纹，使每个 Chunk 可以回到对应原文。

### 子任务

1. 在 `src/starter_agent/knowledge/models.py` 增加：
   - `ParsedBlock`
   - `ParsedDocument`
   - `KnowledgeChunk`
   - `SourceLocation`
2. 新建 `src/starter_agent/knowledge/parser.py`：
   - 将 CRLF/CR 统一为 LF。
   - 去除 UTF-8 BOM。
   - 识别 ATX 标题、Setext 标题、段落、列表、表格、引用块和代码围栏。
   - 保留每个块的 `section_path`、`start_line` 和 `end_line`。
   - 为检索文本建立独立 Normalize 结果，不覆盖引用原文。
3. 新建 `src/starter_agent/knowledge/chunker.py`：
   - 优先按标题和段落边界切分。
   - 目标长度默认 1,200 字符，上限 1,800 字符，重叠默认 150 字符。
   - 代码围栏、表格和列表尽量保持整体。
   - 超长单段按句子或安全字符边界切分。
   - 空白块和只有标题的块不生成 Chunk。
4. Chunk 至少保存：
   - `document_id`
   - `version_id`
   - `version`
   - `filename`
   - `page=null`
   - `section_path`
   - `start_line`
   - `end_line`
   - `user_id`
   - `project_id`
   - `knowledge_base_id`
   - `ordinal`
   - `text`
   - `search_text`
   - `content_sha256`
   - `created_at`
5. 在 `src/starter_agent/knowledge/store.py` 增加 `knowledge_chunks` 表及批量写入、分页预览和按版本清理方法。
6. 新建 `src/starter_agent/knowledge/ingestion.py`，串联：

```text
Upload → Parse → Normalize → Chunk → Metadata
```

7. 入库失败不得留下可供后续索引的半成品 Chunk。
8. 在 API 增加：
   - `GET /v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/chunks`
9. Chunk 预览接口使用 cursor 或稳定的 `ordinal` 分页，只返回受限长度预览，不返回整份文档。
10. 增加固定 Markdown fixture，覆盖中文标题、英文标题、列表、表格、代码块和跨行内容。

### 依赖关系

依赖 Task1 的 Scope、文档、版本、任务模型、Store 和上传 API。

### 验收标准

- 固定 Markdown 在重复运行时产生相同的 Chunk 顺序、标题路径、行号范围和内容指纹。
- Chunk 的引用片段能在对应版本原文和行号范围内找到。
- 表格、代码围栏和列表不会在普通长度下被任意拆断。
- 空文件、只有标题的文件和没有可索引正文的文件返回 `document_no_indexable_content`。
- 解析或 Chunk 失败时任务显示明确错误，且文档不可进入检索。
- 文档详情返回正确的 `chunk_count`，预览接口按稳定顺序分页。
- 通过以下测试：

```powershell
uv run pytest tests/unit/test_markdown_parser.py tests/unit/test_knowledge_chunker.py tests/unit/test_knowledge_store.py tests/integration/test_knowledge_api.py -q
```

### 预估复杂度

高。主要难点是 Markdown 结构边界、原始行号映射和稳定分段。

## Task3：实现 FTS5 索引、BM25 排序和元数据过滤检索

### 任务目标

在不使用 Embedding 或模型检索的前提下，实现可独立测试的全文索引与 Retrieval API，支持 top-k、权限过滤、文档过滤和可定位 `source_ref`。

### 子任务

1. 新建 `src/starter_agent/knowledge/index.py`，定义 `TextIndex` 接口和 `SQLiteFtsIndex` 实现。
2. 初始化 FTS5 虚拟表 `knowledge_chunks_fts`：
   - 使用 `tokenize='trigram'`。
   - 使用 external-content 模式索引 `search_text` 和扁平化 `section_path`。
   - 为 `knowledge_chunks` 增加内部整数 `row_id`，FTS5 通过 `content_rowid='row_id'` 关联。
   - 对外仍使用 UUID `chunk_id`，不得把内部 rowid 暴露为引用标识。
3. 启动时执行 FTS5/trigram 能力自检；不支持时返回明确配置错误，不得静默改为无索引扫描。
4. 在入库流水线加入 `Text Index` 阶段：

```text
Upload → Parse → Normalize → Chunk → Metadata → Text Index
```

5. 索引写入与版本激活使用事务边界；索引失败时该版本不可检索。
6. 新建 `src/starter_agent/knowledge/query.py`：
   - 规范化用户查询。
   - 对 FTS5 特殊字符进行安全处理。
   - 生成参数化 MATCH 查询，禁止拼接未转义表达式。
   - 支持小型、显式求职领域同义词表，例如：
     - `JD / 职位描述 / 岗位要求`
     - `RAG / 检索增强 / 知识库`
     - `LLM / 大语言模型`
   - 对 1～2 字有效关键词使用同一 Scope 下、带候选上限的 `instr(search_text, :term)` 补充查询，不允许无界全库扫描。
7. 新建 `src/starter_agent/knowledge/retrieval.py`，实现：
   - Scope 过滤。
   - 仅检索活动、已索引版本。
   - `document_ids`、`document_types`、`filenames` 和 `versions` 过滤。
   - SQLite FTS5 `bm25()` 值越小越相关，必须按升序排列。
   - 使用稳定排序键依次处理：指定文档、完整短语、标题命中、BM25 升序、文档 ID 和 Chunk ordinal。
   - 不把 BM25、布尔命中和其他量纲合成不透明的 combined score。
   - top-k 限制和稳定的并列排序。
8. Retrieval 返回：
   - `chunk_id`
   - `source_ref`
   - 文档、版本、章节、行号和预览
   - `bm25_score`
   - `matched_terms`
   - `rank`
9. 在 API 增加：
   - `POST /v1/knowledge-bases/{knowledge_base_id}/retrieve`
10. 明确区分：
    - 正常无命中：`200`、`status=no_evidence`、空 matches。
    - 索引或查询失败：非 2xx、稳定错误码。
    - FTS5 或 trigram 不可用：`fts5_unavailable` 或 `fts5_trigram_unavailable`。
11. 建立固定 Retrieval 评测集，记录 query 与预期 Chunk ID，不调用 Generation。

### 依赖关系

依赖 Task2 的稳定 Chunk、元数据和入库流水线。

### 验收标准

- 两份固定安全文档都能建立 FTS5 索引。
- 查询能返回预期 top-k Chunk、`source_ref` 和完整定位元数据。
- 中文连续文本、英文短语和已配置同义词查询均有覆盖测试。
- 1～2 字短查询只在同一 Scope 和候选上限内执行补充检索。
- `document_ids`、类型、文件名和版本过滤生效。
- BM25 按升序解释，并使用稳定并列排序键。
- Scope 过滤在 SQL/索引查询阶段生效，不会先召回其他用户内容再在应用层剔除。
- FTS 查询特殊字符不会造成语法注入或越权查询。
- 无证据与 Retrieval 故障可明确区分。
- Retrieval 测试不初始化或调用任何生成模型。
- 通过以下测试：

```powershell
uv run pytest tests/unit/test_knowledge_query.py tests/unit/test_fts_index.py tests/unit/test_knowledge_retrieval.py tests/integration/test_knowledge_retrieval_api.py -q
```

### 预估复杂度

高。涉及 FTS5 生命周期、中文 trigram、BM25 分数方向、查询安全和权限过滤。

## Task4：实现文档更新、硬删除和索引残留验证

### 任务目标

完成文档生命周期管理：新版本成功后原子替换旧版本；删除开始即停止检索，完成后正文、Chunk、FTS 和关联元数据全部不可恢复检索。

### 子任务

1. 在 API 增加：
   - `PUT /v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/content`
   - `DELETE /v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}`
2. 更新请求使用 `If-Match: <active-content-sha256>`，版本不一致返回 `409 document_version_conflict`。
3. 更新流程：
   - 创建新版本。
   - 独立完成 Parse、Normalize、Chunk、Metadata 和 Text Index。
   - 新版本失败时保留旧版本正常检索。
   - 新版本成功后在单一事务中激活新版本、撤下旧版本并清理旧正文、Chunk 和 FTS。
4. 新建 `invalidated_citations` 表，只保存旧 `chunk_id` 的单向哈希、Scope 和失效时间，不保存正文、文件名、章节、行号或引用片段。
5. 增加引用解析基础接口：
   - 活动 Chunk 返回 canonical 定位信息。
   - 被更新替代且存在同 Scope 失效记录的旧 Chunk 返回 `410 citation_gone`。
   - 未知或越权 Chunk 返回 404。
6. 删除流程：
   - 将文档置于不可检索条件。
   - 在同一事务中删除 FTS、Chunk、版本正文、任务、失效引用记录和文档元数据。
   - 事务失败时保持不可检索并允许幂等重试。
7. SQLite 连接启用外键与 `PRAGMA secure_delete=ON`。
8. 删除成功前执行反向验证：
   - 按旧 `document_id` 无记录。
   - 按旧 `chunk_id` 无记录。
   - 唯一关键词 FTS 无命中。
   - 新 Retrieval 请求无旧 Chunk。
9. 重复 DELETE 保持幂等语义，不泄露其他 Scope 对象。
10. 增加更新与删除并发测试，覆盖更新过程中查询、重复删除和索引失败回滚。

### 依赖关系

依赖 Task1 至 Task3 的版本、Chunk、FTS 索引和 Retrieval。

### 验收标准

- 新版本全部建好前，旧版本继续稳定可检索。
- 新版本激活后，旧 Chunk 和旧内容不再出现在默认检索中。
- 新版本失败不会产生两个活动版本，也不会破坏旧版本。
- 删除完成后使用原问题、唯一关键词、旧文档 ID 和旧 Chunk ID 均不能检索旧内容。
- 重启应用并新建会话后重复检索，旧内容仍不可用。
- 删除失败不会被 API 或界面误报为成功。
- 一个 Scope 不能更新或删除另一个 Scope 的文档。
- 通过以下测试：

```powershell
uv run pytest tests/unit/test_knowledge_lifecycle.py tests/unit/test_fts_index.py tests/integration/test_knowledge_update_delete.py -q
```

### 预估复杂度

高。核心风险是版本原子切换、FTS 与关系表一致性、删除事务和反向验证。

## Task5：实现前端一级“知识库”页面

### 任务目标

在 Starter Agent 导航中提供直接可访问的“知识库”入口，让用户能够完成上传、查看入库状态、文档列表、Chunk 数量、Chunk 预览、更新和删除。

### 子任务

1. 修改 `src/web/index.html`，在左侧一级导航增加：
   - “对话”
   - “知识库”
2. 新增知识库视图，不能放入现有“设置/长期记忆”弹窗中。
3. 页面增加：
   - Markdown 文件选择。
   - 资料类型选择。
   - 授权确认框。
   - 禁止上传敏感资料提示。
   - “上传并入库”按钮。
4. 增加入库任务显示：
   - 当前处理阶段。
   - 成功或失败结果。
   - 安全错误消息。
   - 页面刷新后的任务恢复查询。
5. 增加文档列表：
   - 文件名。
   - 版本。
   - 指纹摘要。
   - 创建时间。
   - 业务状态。
   - Chunk 数量。
6. 增加 Chunk 预览：
   - Chunk ID。
   - 标题路径。
   - 行号范围。
   - 受限原文片段。
   - 分页或“加载更多”。
7. 增加更新动作，使用当前内容指纹作为 `If-Match`。
8. 增加删除确认对话框，明确展示目标文件名和版本。
9. 上传失败、解析失败、Chunk 失败、索引失败、查询状态失败和删除失败必须显示不同信息，不允许空 catch 或静默失败。
10. 使用 `textContent` 渲染文件名和文档内容，禁止把不可信 Markdown 写入 `innerHTML`。
11. 为状态区域添加 `aria-live`，所有按钮、文件选择和预览支持键盘操作。
12. 新增 `tests/unit/test_knowledge_ui_contract.py`，检查入口、控件、API 路径、失败提示和删除确认契约。

### 依赖关系

依赖 Task1 至 Task4 的上传、文档、任务、Chunk、更新和删除 API。

### 验收标准

- 主界面直接显示“知识库”入口，不需要手工输入隐藏 URL。
- 用户能够选择 Markdown、确认授权并提交上传。
- 页面刷新后能够恢复显示文档和入库结果。
- 文档列表显示 Chunk 数，用户能够查看可定位 Chunk 预览。
- 更新携带当前指纹，版本冲突显示明确提示。
- 删除前要求确认；删除失败不会提前从列表消失或显示成功。
- 网络失败、上传失败、解析失败和索引失败均有明确可见信息。
- 文件名和 Chunk 内容不能造成 HTML/脚本注入。
- 通过以下测试：

```powershell
uv run pytest tests/unit/test_knowledge_ui_contract.py tests/integration/test_knowledge_api.py -q
```

### 预估复杂度

高。当前前端为单文件 HTML/CSS/JavaScript，需要谨慎保持视图、状态和错误处理边界。

## Task6：实现受约束 Generation 和服务端引用组装

### 任务目标

把 Retrieval 证据交给生成模型，在不暴露工具能力的情况下生成结构化结论，并由服务端校验和组装真实、可定位的引用。

### 子任务

1. 在 `src/starter_agent/knowledge/models.py` 增加：
   - `Evidence`
   - `GeneratedClaim`
   - `Citation`
   - `RagAnswer`
2. 新建 `src/starter_agent/knowledge/context.py`：
   - 为本次证据分配临时 `evidence_id`。
   - 加入明确证据边界。
   - 标注文档、版本、Chunk、章节和行号。
   - 明确 Chunk 内容是资料，不是系统指令。
   - 只加入本次 Retrieval 返回且通过 Scope 的证据。
3. 新建 `src/starter_agent/knowledge/generation.py`：
   - 复用 `ProviderRegistry`。
   - 调用 `Provider.complete(..., tools=[])`。
   - 要求输出 `answered/refused/conflict`、answer、claims 和 evidence IDs。
   - JSON 解析失败最多进行一次格式修复。
4. 新建 `src/starter_agent/knowledge/citations.py`：
   - 只接受本次 Evidence 中存在的 ID。
   - 校验每个事实性 claim 至少有一个证据。
   - 校验 quote 是对应 Chunk 原文的连续子串。
   - 从 Store 中读取 canonical 元数据，模型不得直接决定文件名、版本、行号或 quote。
5. 最终 Citation 至少返回：
   - `citation_id`
   - `document_id`
   - `filename`
   - `document_version`
   - `chunk_id`
   - `page`
   - `section`
   - `start_line`
   - `end_line`
   - `quote`
6. 在 API 增加：
   - `POST /v1/knowledge-bases/{knowledge_base_id}/answer`
7. 回答中的引用必须放在对应结论附近，同时响应保留结构化 `claims` 与 `citations`。
8. Generation 独立测试直接注入固定 Evidence 和 Stub Provider，不调用 FTS5。
9. 测试提示注入 Chunk，确认 Generation 不调用工具、不改变系统约束。

### 依赖关系

依赖 Task3 的 Retrieval 数据契约和 Task4 的 canonical 引用解析。

### 验收标准

- 固定 Evidence 能生成与证据一致的答案和可定位引用。
- 模型不能引用本次 Evidence 之外的 Chunk。
- 模型伪造文件名、版本、行号或 quote 不会进入最终响应。
- 一个回答包含多个事实时，每个事实具有相邻、实际支持它的引用。
- 引用片段能够在对应文档版本和行号中找到。
- Generation 测试不调用 Retrieval、FTS5 或上传流程。
- 模型输出格式错误或引用校验失败时，不展示未经校验的回答。
- Generation 调用始终使用 `tools=[]`。
- 通过以下测试：

```powershell
uv run pytest tests/unit/test_rag_context.py tests/unit/test_rag_citations.py tests/unit/test_rag_generation.py tests/integration/test_rag_answer_api.py -q
```

### 预估复杂度

高。难点是结构化模型输出、claim-to-evidence 约束、引用防伪和失败降级。

## Task7：实现证据充分性、拒答、冲突处理和 Chat 接入

### 任务目标

确保无可靠证据时稳定拒答，并让现有聊天入口能够显式选择知识库进行受约束问答，而不是让通用 Agent 自行决定是否检索。

### 子任务

1. 新建 `src/starter_agent/knowledge/evidence.py`，实现 `EvidenceSufficiencyGate`：
   - 无命中返回 `no_evidence`。
   - 关键词覆盖不足返回 `insufficient_evidence`。
   - 问题中的关键数字或专有实体在证据中不存在时不支持确定性结论。
   - 多份活动文档存在明显冲突时保留双方证据并标记 `conflict`。
2. 在 Generation 前执行 Gate；无证据或证据不足时不调用模型。
3. 在 Generation 后验证：
   - 每个私人事实有有效 citation。
   - 只能回答证据支持的部分。
   - 无支持部分转换为明确拒答说明。
4. 统一拒答文案至少包含“知识库中没有足够证据”，并返回机器可判定的 `refusal_reason`。
5. 扩展 `ChatRequest`：

```json
{
  "knowledge_base_id": "可选 UUID",
  "knowledge_mode": "off | required"
}
```

6. `knowledge_mode=required` 时由 `KnowledgeApplicationService.answer()` 处理当前问题，并将已校验答案与引用保存到会话。
7. `knowledge_mode=off` 时保持现有聊天、工具、邮件和长期记忆行为。
8. 第一阶段不实现不透明的 `auto` 模式，避免模型自行跳过检索。
9. 前端聊天区域增加可选知识库开关或选择器；开启后请求携带 `knowledge_mode=required`。
10. 知识库回答不得自动触发邮件发送、简历写入或其他 `write/external` 工具。
11. 增加以下固定拒答测试：
    - 不存在的 HR 邮箱。
    - 不存在的工作经历。
    - 不存在的技能。
    - 文档没有提供的成果数字。
    - 只有一半问题有证据。

### 依赖关系

依赖 Task3 的检索结果、Task5 的前端导航能力和 Task6 的受约束 Generation。

### 验收标准

- Retrieval 无命中时不调用模型并明确拒答。
- 相关性或关键词覆盖不足时不把相似文本当成证据。
- 部分有证据时只回答有证据部分，并拒绝其余部分。
- 冲突文档被同时引用并明确说明，不能静默选择一个版本。
- Chat 开启知识库后返回相同的受约束答案和引用契约。
- Chat 关闭知识库后不改变现有行为。
- RAG 问答不会触发写入或外部工具。
- 通过以下测试：

```powershell
uv run pytest tests/unit/test_evidence_gate.py tests/unit/test_rag_refusal.py tests/integration/test_rag_chat.py tests/unit/test_knowledge_ui_contract.py -q
```

### 预估复杂度

高。拒答必须同时稳定、可解释且不过度误拒，Chat 接入还需保持现有会话行为兼容。

## Task8：完成安全、日志、容量和故障恢复加固

### 任务目标

补齐个人知识库的生产边界，确保权限过滤、日志脱敏、容量限制、任务中断恢复和并发操作不会破坏引用或删除保证。

### 子任务

1. 扩展 `src/starter_agent/observability/logging.py` 的敏感字段规则，覆盖：
   - `document_text`
   - `chunk_text`
   - `search_text`
   - `question`
   - `quote`
   - `upload_content`
2. 新建知识库审计字段白名单，只允许记录：
   - 操作类型。
   - 文档和任务 ID。
   - 内容指纹前缀。
   - 阶段。
   - Chunk 数。
   - 耗时。
   - 错误码。
3. 校验单文件 2 MiB、每知识库 100 份活动文档、最多 5,000 个活动 Chunk 的默认限制。
4. 入库任务在数据库持久化：
   - 排队任务在应用重启时重新调度。
   - 中间阶段中断任务标记为 `ingestion_interrupted`。
   - 重试前清理未激活半成品。
5. 为索引、更新和删除增加每文档互斥或数据库级冲突保护，防止同一文档并发更新产生双活动版本。
6. 测试恶意文件名、Markdown HTML、提示注入、FTS 查询语法字符和越权 ID。
7. 使用唯一非真实敏感标记扫描：
   - 普通日志。
   - API 错误。
   - SSE 输出。
   - pytest 失败输出和快照。
8. 验证外部 Provider 只收到问题与本次证据，不收到全库、其他用户资料或文件系统路径。
9. 更新 `config/config.example.yaml` 注释，明确不得放入秘密或私人正文。

### 依赖关系

依赖 Task1 至 Task7 的完整后端、前端和问答链路。

### 验收标准

- 普通日志、错误响应和前端控制台不包含测试正文、问题、quote 或模拟秘密。
- 容量超限返回稳定错误，不创建半成品索引。
- 应用重启后排队任务可恢复，中断任务可解释并可安全重试。
- 并发更新不会产生两个活动版本。
- 未授权 Scope 无法查看、预览、检索、更新或删除其他 Scope 文档。
- 提示注入文档不能触发工具或改变权限。
- FTS 查询特殊字符不能造成 SQL/FTS 注入。
- 通过以下测试：

```powershell
uv run pytest tests/unit/test_knowledge_security.py tests/unit/test_logging_security.py tests/unit/test_knowledge_recovery.py tests/integration/test_knowledge_isolation.py -q
```

### 预估复杂度

中高。主要是跨模块安全验证、恢复流程和并发一致性。

## Task9：完成端到端测试、独立评测和验收文档

### 任务目标

用固定、无敏感信息的数据证明知识库满足前端入口、入库、Chunk 预览、Retrieval、带引用回答、拒答、更新和删除要求，不以“回答看起来合理”作为通过依据。

### 子任务

1. 新增两份固定安全 fixture：
   - `tests/fixtures/knowledge/resume_demo.md`
   - `tests/fixtures/knowledge/job_demo.md`
2. Fixture 使用虚构姓名、虚构公司和 `.test` 域名，不包含真实 API Key、邮箱密码、授权码或身份证件信息。
3. 新增 Retrieval 独立评测：
   - 固定查询。
   - 固定预期 Chunk ID 或内容指纹。
   - 断言 top-k、来源、版本、章节和行号。
   - 断言无证据 query 返回空命中。
4. 新增 Generation 独立评测：
   - 固定 Evidence。
   - 固定 Stub Provider。
   - 断言答案只使用给定证据。
   - 空 Evidence 和无关 Evidence 必须拒答。
5. 新增端到端 API 场景：
   - 上传两份 Markdown。
   - 等待完成索引。
   - 查看文档列表和 Chunk 预览。
   - 提问有证据问题并核对引用。
   - 提问无证据问题并核对拒答。
   - 更新文档并验证旧 Chunk 失效。
   - 删除文档并验证旧内容不可检索。
6. 删除验证同时使用：
   - 原问题。
   - 唯一关键词。
   - 旧 `document_id`。
   - 旧 `chunk_id`。
   - 应用重启后的新会话。
7. 新增前端人工验收步骤，覆盖一级入口、键盘操作、上传进度、失败提示、预览和删除确认。
8. 新建 `docs/rag-acceptance.md`，记录：
   - 环境准备。
   - 固定测试数据。
   - 自动化测试命令。
   - 人工验收步骤。
   - 每项可观察证据。
   - 明确禁止使用真实秘密或私人资料。
9. 更新 `README.md`，只加入知识库启动、访问和安全测试数据说明，不加入真实配置值。
10. 运行完整回归，确保现有会话、长期记忆、简历、岗位搜索和邮件测试未被破坏。

### 依赖关系

依赖 Task1 至 Task8 的全部实现。

### 验收标准

- Starter Agent 前端存在明确、可直接访问的“知识库”入口。
- 两份固定安全 Markdown 成功入库并显示正确 Chunk 数。
- Chunk 预览与原文标题、行号和片段一致。
- 有证据问题返回正确答案和可定位引用。
- 无证据问题稳定拒答。
- 更新后旧 Chunk 不再检索，旧引用失效。
- 删除后旧内容在重启和新会话中仍不可检索。
- Retrieval 与 Generation 分别通过独立测试。
- 日志和测试产物中不存在真实秘密、不必要的私人正文或唯一测试敏感标记。
- 全部回归测试通过：

```powershell
uv run pytest -q
```

- 验收文档明确说明：模型流畅、模型自称已检索或回答看起来合理，都不能单独作为通过依据。

### 预估复杂度

中高。工作量集中在跨层端到端夹具、删除残留验证、前端人工验收和完整回归。

## 顺序执行约束

实施必须按 `Task1 → Task2 → Task3 → Task4 → Task5 → Task6 → Task7 → Task8 → Task9` 推进。后续 Task 可以读取前序 Task 的稳定接口，但不得提前修改尚未通过验收的前序数据契约。

每个 Task 交付时必须提供以下执行报告：

1. 修改和新增的文件。
2. 实际运行的测试命令及通过/失败数量。
3. 对照该 Task 验收标准的逐项结论。
4. 已知限制、未消除风险和对下一 Task 的影响。

只有在用户明确说“确认计划，开始执行”后，才进入代码实施。
