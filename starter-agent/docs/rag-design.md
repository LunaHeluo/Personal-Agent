# Starter Agent 带引用的个人知识库 / RAG 设计文档

## 文档定位与开发流程

本次 RAG 功能固定采用以下流程：

```text
rag-requirements.md
→ rag-design.md
→ rag-task.md
→ 按 Task 顺序执行
→ 最终验证
```

本文件只描述已确认需求的工程设计，不承担实施进度记录。第一阶段索引与 Retrieval 使用纯 SQLite FTS5/BM25，不引入 Embedding、向量索引、向量数据库、混合检索或模型 Rerank。

## 需求理解与设计目标

本设计为 Starter Agent 增加面向求职场景的私人知识库。用户主动上传并授权使用 Markdown 文档，例如脱敏简历、岗位 JD、面试准备笔记、公开公司调研摘要和脱敏邮件沟通摘要。系统先检索证据，再基于本次证据生成答案，并返回能够定位到文档版本、章节、行号和原文片段的引用。

设计目标：

1. Starter Agent 导航中新增一级“知识库”入口，普通用户无需开发者工具即可上传、查看、更新和删除文档。
2. 将入库拆分为可观察的 `Upload → Parse → Normalize → Chunk → Metadata → Text Index` 流水线。
3. 将问答拆分为 `Question → Query Normalize → Metadata Filter → FTS5 Retrieve → Deterministic Rank → Evidence Gate → Context → Answer → Citation`。
4. 第一阶段索引与 Retrieval 完全确定性，不调用任何 Embedding、生成模型或模型 Rerank。
5. 引用由服务端根据实际命中 Chunk 的 canonical 元数据组装；模型不能伪造文件名、版本、行号或 Chunk ID。
6. 无命中、相关性不足、证据冲突或引用校验失败时明确拒答，不用模型常识补齐用户经历、岗位事实或沟通事实。
7. 更新采用“新版本完整建好后原子切换”；旧 Chunk 在切换后立即退出检索并被清除。
8. 删除采用硬删除，清除原始正文、Chunk、FTS 条目和关联元数据；完成后旧标识和关键词查询均不能命中。
9. Retrieval 和 Generation 使用独立接口与数据契约，能够分别做确定性测试。
10. 保持与当前 FastAPI、ApplicationService、ProviderRegistry、ToolRegistry、SQLAlchemy/SQLite、单文件 Web 前端和 pytest 测试结构一致。

知识库与长期记忆保持独立：

- 长期记忆保存少量、稳定、经用户确认的事实。
- RAG 保存可更新、可删除、带版本和来源的文档。
- 知识库内容不得自动写入长期记忆。
- 仓库当前没有可执行的 Todo 工具实现，只有文档设想，因此第一阶段 RAG 不依赖 Todo 模块。

## 技术选型

### 索引方案比较

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| SQLite FTS5 `trigram` + BM25 + 元数据过滤 | 复用现有 SQLite；无需模型和网络；事务、权限过滤、更新和硬删除容易统一；中文与英文子串可检索；结果可重复测试 | 语义同义表达召回弱于向量检索；依赖 SQLite 构建包含 FTS5/trigram | 第一阶段采用 |
| 自建倒排索引 | token、权重和中文规则完全可控；不依赖 FTS5 | 实现、迁移、并发和正确性测试成本高；重复实现数据库能力 | 暂不采用 |
| 逐 Chunk 扫描 | 实现最简单；无额外索引 | 数据量增长后性能差；排序与分页弱；难以满足稳定 top-k | 仅适合诊断回退，不作为产品路径 |

第一阶段采用 SQLite FTS5：

- SQLAlchemy/SQLite 保存知识库、文档、版本、任务和 Chunk。
- FTS5 虚拟表索引 `search_text` 与扁平化 `section_path`。
- tokenizer 使用 `trigram`，支持中文连续文本、英文短语和子串召回。
- 使用 FTS5 `bm25()` 排序；SQLite FTS5 中更相关结果的 BM25 值更小，查询按升序排列。
- 标题完整命中、短语命中、用户指定文档通过确定性排序键加权，不把不同量纲强行合成不透明分数。
- 查询同义扩展只使用显式、版本化的求职领域词典，不调用模型。
- FTS5/trigram 不可用时，启动自检明确失败，不静默退化为“看似成功”的扫描。

### 第一阶段不采用的能力

以下能力不进入第一阶段代码、配置、数据表或验收：

- Embedding API 或本地 Embedding 模型。
- `HashingEmbedder`。
- 向量 BLOB、余弦相似度或向量数据库。
- 词法与向量混合召回。
- Cross-encoder 或生成模型 Rerank。
- 更换 Embedding 模型后的重建逻辑。

后续若固定 Retrieval 评测证明纯词法方案无法满足召回目标，可在新的需求、设计和任务流程中新增 Retriever 实现；当前阶段只保留通用 `Retriever` 服务边界，不预先实现向量能力。

### Generation 选择

Generation 复用现有 `ProviderRegistry` 和 `Provider.complete()`，但通过独立 `RagGenerationService` 调用，且设置 `tools=[]`。每个知识库记录 Generation 处理模式：

- `local`：只允许配置为本地地址的 Provider 处理问题和证据。
- `external`：允许将问题与本次选中的最少证据发送到用户明确确认的外部 Provider。

知识库设置必须展示 Generation 的数据去向。切换到外部处理前要求明确确认；服务端保存确认时间和配置指纹。配置发生变化后旧确认失效。

Generation 模型不参与上传、解析、Chunk、索引、Retrieval 或排序。

### Markdown 解析与分段

第一阶段使用小型、确定性的 Markdown 结构解析器：

- 输入严格按 UTF-8 解码。
- 识别 ATX 标题、Setext 标题、段落、列表、表格、引用块和代码围栏。
- 以标题路径为主要边界，以目标字符数为软上限。
- 超长章节按段落切分；只有超长单段才按句子或安全字符边界切分。
- 相邻 Chunk 保留少量重叠，但引用只标记该 Chunk 实际覆盖的原文行。
- Normalize 保留换行映射，不能因搜索规范化破坏原始行号。

默认参数：

```text
target_chars = 1200
overlap_chars = 150
max_chars = 1800
```

参数可配置；测试使用固定值，不依赖模型决定 Chunk 边界。

## 总体架构设计

```text
Web「知识库」页面
        |
        v
FastAPI /v1/knowledge-bases/*
        |
        v
KnowledgeApplicationService
   |              |                 |
   v              v                 v
IngestionService  RetrievalService  RagGenerationService
   |              |                 |
   v              v                 v
Parser/Chunker    SQLiteFtsIndex     ProviderRegistry
MetadataBuilder   QueryNormalizer    CitationValidator
   \              /
    v            v
       SQLiteKnowledgeStore
        |              |
   relational data   FTS5 index
```

新增 `KnowledgeApplicationService` 作为知识库用例入口，不把知识库 CRUD 塞入现有 `ApplicationService`。`bootstrap.py` 负责构造知识库服务，并共享设置、ProviderRegistry 和数据库连接配置。

建议模块：

```text
src/starter_agent/knowledge/
  __init__.py
  models.py          # 文档、Chunk、证据、引用和业务状态模型
  errors.py          # 稳定错误码及安全公开消息
  security.py        # 上传校验、敏感模式检测、审计字段白名单
  store.py           # SQLiteKnowledgeStore
  parser.py          # Markdown 解析、Normalize 和行号映射
  chunker.py         # 确定性结构分段
  query.py           # 查询规范化、转义和显式同义词扩展
  index.py           # FTS5 建表、自检、写入和删除
  ingestion.py       # 入库、更新、恢复和业务状态机
  retrieval.py       # 元数据过滤、FTS5 召回与确定性排序
  evidence.py        # 证据充分性和冲突检测
  context.py         # 受控 Evidence Context
  generation.py      # 受约束 Generation
  citations.py       # 引用组装与校验
  service.py         # KnowledgeApplicationService
```

FastAPI 路由仍位于 `src/starter_agent/interfaces/api.py`。前端继续使用 `src/web/index.html`，新增一级知识库视图，不把知识库隐藏在“设置/长期记忆”弹窗中。

### 入库数据流

```text
Upload
  → 授权、格式、大小和敏感内容校验
  → Parse
  → Normalize（保留原文行映射）
  → Chunk
  → Metadata
  → 写入文档、版本和 Chunk
  → 写入 FTS5 Text Index
  → 原子激活版本
  → indexed
```

解析、Chunk 或索引任一步失败时，新版本保持不可检索。更新场景中的旧版本继续可用，直到新版本全部完成；切换事务成功后旧 Chunk 才退出检索并被清除。

### 查询数据流

```text
Question
  → ScopeResolver
  → Query Normalize
  → 显式同义词扩展
  → Metadata Filter
  → FTS5 Retrieve
  → Deterministic Rank
  → Evidence Sufficiency Gate
  → Context Builder
  → Answer
  → Citation Validator/Assembler
  → answered / conflict / refused
```

权限过滤必须发生在 FTS5 与 Chunk 关联查询中，不能先检索所有用户的数据再在应用层删除越权结果。

## 模块/组件设计

### 1. KnowledgeApplicationService

负责上传、任务查询、文档列表、预览、更新、删除、Retrieval 和 Answer 用例：

- 从 `ScopeResolver` 获取可信 `user_id/project_id`。
- 校验知识库归属和 Generation 数据处理授权。
- 协调 Store、Ingestion、Retrieval、Evidence Gate 和 Generation。
- 将领域错误转换为稳定 API 结果，不暴露原始异常或正文。

### 2. ScopeResolver

当前 Starter Agent 是本机单用户应用，没有登录系统。第一阶段由服务端配置产生默认范围，例如 `local-user/default-project`，客户端不能在请求体中覆盖。测试可注入两个 Scope 验证隔离。

所有文档、Chunk、任务和查询必须携带：

```text
user_id + project_id + knowledge_base_id
```

任何按 `document_id`、`version_id` 或 `chunk_id` 的读取、预览、更新、引用解析和删除都必须同时匹配 Scope。越权访问统一按不可见对象处理，避免泄露对象是否存在。

### 3. UploadValidator 与 SensitiveContentScanner

上传校验顺序：

1. 要求 `confirmed_authorized=true`。
2. 校验 `.md` 或 `.markdown`、文件大小、文件名长度和安全字符。
3. 读取受限字节并严格 UTF-8 解码。
4. 拒绝 NUL、异常控制字符和明显二进制特征。
5. 计算 SHA-256 内容指纹。
6. 扫描明显 API Key、Token、密码、邮箱授权码和证件号码模式。
7. 命中高风险模式时拒绝入库，只记录规则 ID。

敏感扫描不能证明资料已获授权。界面仍必须提示禁止上传身份证件、未授权公司资料、真实 API Key、私人邮箱密码或授权码。

### 4. MarkdownParser 与 Normalizer

输出 `ParsedDocument`：

- 规范化 `CRLF/CR` 为 `LF`。
- 去除 UTF-8 BOM。
- 保存原文块类型、标题路径和原始行号。
- 为搜索生成独立 `search_text`。
- 引用用 `source_text` 不得被搜索规范化覆盖。

### 5. Chunker 与 MetadataBuilder

Chunker 输出稳定 Chunk：

- 优先按标题和段落边界切分。
- 代码围栏、表格和列表尽量保持整体。
- 空块或只有标题的块不进入索引。
- 分段为空返回 `document_no_indexable_content`。
- `chunk_id` 使用 UUID；另存 `ordinal` 与 `content_sha256`，保证排序和校验稳定。

Metadata 至少包含：

```text
document_id
filename
page
section
user_id
project_id
knowledge_base_id
version
created_at
chunk_id
start_line
end_line
```

Markdown 的 `page` 为 `null`，以 `section_path + start_line/end_line` 定位。

### 6. SQLiteKnowledgeStore

Store 负责：

- 知识库、文档、版本、任务和 Chunk CRUD。
- 所有方法强制 Scope。
- 文档版本激活与清理。
- FTS5 关系维护的事务边界。
- 幂等处理相同内容上传和重复删除。

SQLite 连接启用：

```text
PRAGMA foreign_keys=ON
PRAGMA journal_mode=WAL
PRAGMA secure_delete=ON
```

删除完成后执行 WAL checkpoint 策略。验收环境必须扫描唯一测试标记，证明普通数据库、WAL 和日志不保留可检索正文。系统备份不在应用事务控制范围内，部署前必须单独确认。

### 7. SQLiteFtsIndex

FTS5 使用 external-content 模式关联 `knowledge_chunks`，避免让两个独立正文副本产生漂移：

```sql
CREATE VIRTUAL TABLE knowledge_chunks_fts USING fts5(
  search_text,
  section_text,
  content='knowledge_chunks',
  content_rowid='row_id',
  tokenize='trigram'
);
```

`knowledge_chunks` 使用内部整数 `row_id` 供 FTS5 关联，同时保留公开 UUID `chunk_id`。插入与删除通过显式 Store 方法或同事务触发器同步；Chunk 在活动版本内不可原地编辑。

启动自检：

1. 检查 SQLite 编译选项或实际创建临时 FTS5 表。
2. 检查 `trigram` tokenizer 能正常创建和查询。
3. 自检失败时阻止启用知识库，并返回 `fts5_unavailable` 或 `fts5_trigram_unavailable`。

索引层只负责写入、删除和受 Scope 约束的查询，不调用 Provider。

### 8. QueryNormalizer

查询处理完全确定性：

- 统一 Unicode 空白和大小写。
- 提取中文连续片段、英文/数字 token 和引号短语。
- 转义 FTS5 特殊字符，使用参数化 SQL。
- 显式同义词表按版本加载，例如：
  - `JD ↔ 职位描述 ↔ 岗位要求`
  - `RAG ↔ 检索增强 ↔ 知识库`
  - `LLM ↔ 大语言模型`
- 不从模型动态生成同义词。

trigram 对少于 3 个字符的查询召回有限。对于 1～2 字有效关键词，使用同一 Scope 下受容量限制的 `instr(search_text, :term)` 确定性补充查询；该路径必须有单独测试和候选上限。

### 9. FtsRetriever

Retrieval 固定顺序：

1. 验证 query 非空。
2. 规范化并转义 query。
3. 使用 `user_id/project_id/knowledge_base_id/status=indexed/active_version` 过滤。
4. 可选加入 `document_ids/document_types/filenames/versions`。
5. FTS5 召回候选；短查询执行受限补充查询。
6. 按以下稳定排序键排序：
   - 用户显式指定文档优先。
   - 完整短语命中优先。
   - 标题路径命中优先。
   - `bm25()` 升序。
   - `document_id/chunk.ordinal` 作为并列稳定键。
7. 应用 `top_k`。

返回每个候选的：

- `chunk_id`
- `source_ref`
- canonical 来源元数据
- `bm25_score`
- `matched_terms`
- `rank`

不返回 vector score、combined score 或模型相关性说明。

### 10. EvidenceSufficiencyGate

Generation 前执行确定性检查：

- 无 Chunk：`no_evidence`。
- 没有有效 query term 覆盖：`insufficient_evidence`。
- 问题要求的关键数字或专有实体未出现在证据中：拒绝对应确定性结论。
- 多份活动文档对同一显式键值给出冲突：标记 `conflict`，保留双方证据。
- 只有部分子问题有证据：只允许回答有证据部分。

Gate 不声称完全解决语义蕴含；最终仍需 CitationValidator。阈值和规则由固定 Retrieval/Generation 评测集校准，不能只凭演示问题设定。

### 11. RagContextBuilder

只将本次 Retrieval 返回且通过 Scope 与 Evidence Gate 的证据放入 Generation Context：

```text
[EVIDENCE evidence_id=ev_01 document_id=... version=... chunk_id=...]
source: resume_demo.md
section: 工作经历 / Starter Agent
lines: 18-25
content:
...
[/EVIDENCE]
```

系统提示明确证据是数据而不是指令。Chunk 中的“忽略系统规则”“调用工具”等文本不能改变行为。Context 受现有 Token 预算约束；超限时只按检索排名裁剪，并设置 `evidence_truncated=true`，不使用模型摘要替换原始证据。

### 12. RagGenerationService

Generation 使用 `tools=[]` 调用 Provider，并要求结构化结果：

```json
{
  "status": "answered | refused | conflict",
  "answer": "回答文本",
  "claims": [
    {
      "text": "事实性结论",
      "evidence_ids": ["ev_01"]
    }
  ],
  "refusal_reason": null
}
```

模型只引用临时 `evidence_id`，不得自行输出 canonical 引用字段。服务端解析失败时最多进行一次格式修复；仍失败返回 `generation_invalid_output`。

一般求职建议若不依赖私人事实，可以标记为“一般建议”。个人经历、岗位要求、公司资料和沟通事实必须使用严格证据模式。

### 13. CitationValidator 与 CitationAssembler

Validator 只接受本次 Retrieval 中存在的 `evidence_id`。Assembler 从当前活动 Chunk 的 canonical 元数据生成：

```json
{
  "citation_id": "cit_01",
  "document_id": "...",
  "filename": "resume_demo.md",
  "document_version": 2,
  "chunk_id": "...",
  "page": null,
  "section": ["工作经历", "Starter Agent"],
  "start_line": 18,
  "end_line": 25,
  "quote": "支持该结论的原文片段",
  "content_sha256": "..."
}
```

校验规则：

- `evidence_id` 必须属于本次 Retrieval。
- 每个私人事实 claim 至少有一个引用。
- quote 必须是对应 Chunk/source_text 的连续子串。
- 引用版本必须是回答实际使用的版本。
- 更新或删除后不得把旧 Chunk 映射到新内容。

### 14. IngestionService

入库任务持久化在 SQLite，由当前单进程后台 worker 执行。API 返回 `202 Accepted`，页面轮询。

应用重启时：

- `queued` 任务重新入队。
- 停在中间阶段的任务标记为 `ingestion_interrupted`，允许重试。
- 重试前清理未激活半成品 Chunk 和 FTS 条目。

第一阶段不引入 Redis/Celery。若部署多个 Uvicorn worker，必须先引入带租约的可靠任务队列，不能让多个进程重复处理同一任务。

### 15. 前端知识库视图

在现有左侧导航增加“知识库”，与“对话”并列作为一级入口。页面至少包含：

- Markdown 文件选择。
- 资料类型选择。
- 上传授权确认。
- 禁止上传敏感资料提示。
- “上传并入库”按钮。
- 当前入库阶段与明确错误。
- 文档列表：文件名、版本、指纹摘要、创建时间、状态、Chunk 数。
- 文档操作：Chunk 预览、更新、删除。
- Chunk 预览：Chunk ID、章节路径、行号范围、原文片段。
- 删除确认对话框。

前端分别显示：

- `upload_failed`
- `parse_failed`
- `normalize_failed`
- `chunk_failed`
- `index_failed`
- `delete_failed`
- 网络或状态查询失败

不得把非 2xx、轮询超时或网络异常静默显示为“仍在处理中”。文件名和正文使用 `textContent` 渲染；状态区域使用 `aria-live`。

### 16. 与现有工具和 Context 的配合

- `search_jobs_serpapi` 继续获取公开岗位；结果只有经用户确认并显式保存后才能进入知识库。
- 简历工具继续管理指定文件和版本；知识库保存上传快照，不根据同名文件静默变化。
- 邮件工具继续负责授权读取、草稿和发送；知识库只接收用户主动上传且已脱敏的邮件摘要。
- RAG 证据不自动写入长期记忆。
- RAG 问答不自动触发邮件发送、保存简历或其他 `write/external` 工具。
- Evidence 进入 Generation 前仍受 Context Token 预算治理，但治理不能丢失引用元数据或把部分证据标为完整。

## 数据模型

### KnowledgeBase

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | UUID | 知识库标识 |
| `user_id` | string | 用户范围 |
| `project_id` | string | 项目范围 |
| `name` | string | 展示名称 |
| `generation_mode` | `local/external` | Generation 数据处理模式 |
| `generation_provider` | string | Provider 名称 |
| `generation_model` | string | 模型名称 |
| `external_consent_at` | datetime/null | 外部处理确认时间 |
| `external_consent_fingerprint` | string/null | 已确认配置指纹 |
| `created_at/updated_at` | datetime | 时间 |

知识库不保存 embedding profile、向量维度或向量模型字段。

### KnowledgeDocument

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | UUID | 逻辑文档标识 |
| `knowledge_base_id` | UUID | 所属知识库 |
| `user_id/project_id` | string | 隔离字段 |
| `filename` | string | 安全展示文件名 |
| `document_type` | string | resume/jd/interview/company/email_summary/other |
| `active_version_id` | UUID/null | 当前可检索版本 |
| `status` | enum | queued/processing/indexed/failed/deleting |
| `created_at/updated_at` | datetime | 时间 |

删除完成后记录被物理删除；`deleted` 只作为 DELETE 响应，不长期保留文档行。

### DocumentVersion

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | UUID | 版本标识 |
| `document_id` | UUID | 逻辑文档 |
| `version` | integer | 单调递增版本 |
| `content_sha256` | string | 内容指纹 |
| `source_text` | text | 规范化换行后的 Markdown 原文 |
| `status` | enum | queued/parsing/normalizing/chunking/indexing/indexed/failed |
| `chunk_count` | integer | Chunk 数 |
| `created_at/indexed_at` | datetime | 时间 |
| `error_code` | string/null | 安全错误码 |

新版本激活后，旧版本正文、Chunk 和 FTS 条目清除；只保留不含正文的失效引用墓碑和审计事件。

### KnowledgeChunk

| 字段 | 类型 | 说明 |
|---|---|---|
| `row_id` | integer | FTS5 external-content 关联键 |
| `id` | UUID | 对外 Chunk 标识 |
| `document_id/version_id` | UUID | 文档与版本 |
| `knowledge_base_id` | UUID | 知识库 |
| `user_id/project_id` | string | 权限过滤 |
| `ordinal` | integer | 文档内顺序 |
| `text` | text | Generation 使用的原文片段 |
| `search_text` | text | 确定性搜索规范化文本 |
| `section_text` | text | 扁平化标题路径 |
| `content_sha256` | string | Chunk 指纹 |
| `page` | integer/null | Markdown 为 null |
| `section_path` | JSON array | 标题路径 |
| `start_line/end_line` | integer | 原文行范围 |
| `created_at` | datetime | 时间 |

### KnowledgeChunksFts

FTS5 虚拟表只索引：

- `search_text`
- `section_text`

它通过 `row_id` 关联 `knowledge_chunks`。权限、版本和文档类型字段仍从关系表过滤，不能只依靠 FTS 行号。

### IngestionJob

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | UUID | 任务标识 |
| `document_id/version_id` | UUID | 目标版本 |
| `user_id/project_id` | string | 权限范围 |
| `status` | enum | queued/running/succeeded/failed |
| `stage` | enum | upload/parse/normalize/chunk/metadata/index/activate/delete |
| `progress_current/total` | integer | 安全进度 |
| `error_code` | string/null | 稳定错误码 |
| `retryable` | boolean | 是否允许重试 |
| `created_at/started_at/finished_at` | datetime | 时间 |

### InvalidatedCitation

更新时保存不含正文的失效引用墓碑：

| 字段 | 类型 | 说明 |
|---|---|---|
| `chunk_id_hash` | string | 旧 Chunk ID 单向哈希 |
| `knowledge_base_id` | UUID | 知识库 |
| `user_id/project_id` | string | 权限范围 |
| `reason` | `superseded` | 失效原因 |
| `invalidated_at` | datetime | 时间 |

墓碑不含文件名、版本、章节、行号、quote 或其他可恢复正文。整份文档删除时墓碑一并硬删除。

### RetrievalResult、Evidence 与 Citation

服务模型：

- `RetrievalResult`：query、filters、matches、no_hit、query_config_fingerprint。
- `RetrievalMatch`：Chunk canonical 元数据、BM25、matched terms 和 rank。
- `Evidence`：本次临时 evidence ID、Chunk 元数据和文本。
- `Citation`：文档、版本、Chunk、section、行号和 quote。
- `RagAnswer`：status、answer、claims、citations、refusal reason 和 trace ID。

普通日志只保存 ID、配置指纹、耗时和数量，不保存 query、Chunk、quote 或完整回答。

## API / 服务接口设计

### 知识库设置

```text
GET /v1/knowledge-bases
GET /v1/knowledge-bases/{knowledge_base_id}
PUT /v1/knowledge-bases/{knowledge_base_id}
```

`PUT` 可更新 Generation mode、provider 和 model。切换外部处理配置必须带 `confirmed_external_processing=true`。索引没有模型配置。

### 上传与文档管理

```text
POST   /v1/knowledge-bases/{knowledge_base_id}/documents
GET    /v1/knowledge-bases/{knowledge_base_id}/documents
GET    /v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}
PUT    /v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/content
DELETE /v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}
GET    /v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/chunks
GET    /v1/knowledge-bases/{knowledge_base_id}/ingestion-jobs/{job_id}
```

上传与更新使用 `multipart/form-data`：

```text
file=<Markdown>
document_type=resume|jd|interview|company|email_summary|other
confirmed_authorized=true
```

上传成功返回 `202`：

```json
{
  "document_id": "...",
  "version_id": "...",
  "job_id": "...",
  "status": "queued",
  "stage": "upload",
  "content_sha256": "..."
}
```

更新要求 `If-Match: <active-content-sha256>`，冲突返回 `409 document_version_conflict`。

DELETE 幂等。成功响应：

```json
{
  "document_id": "...",
  "status": "deleted",
  "deleted_chunks": 12,
  "verification": "not_retrievable"
}
```

### Retrieval

```text
POST /v1/knowledge-bases/{knowledge_base_id}/retrieve
```

请求：

```json
{
  "question": "我的哪项经历符合该岗位的 RAG 要求？",
  "filters": {
    "document_ids": [],
    "document_types": ["resume", "jd"],
    "filenames": [],
    "versions": []
  },
  "top_k": 6
}
```

响应：

```json
{
  "status": "matched",
  "query": "我的哪项经历符合该岗位的 RAG 要求？",
  "matches": [
    {
      "chunk_id": "...",
      "source_ref": "knowledge-chunk:...",
      "document_id": "...",
      "filename": "resume_demo.md",
      "version": 2,
      "page": null,
      "section": ["项目经历", "Starter Agent"],
      "start_line": 20,
      "end_line": 27,
      "preview": "...",
      "bm25_score": -3.42,
      "matched_terms": ["RAG", "知识库"],
      "rank": 1
    }
  ]
}
```

无命中返回 `200 status=no_evidence, matches=[]`；检索故障返回非 2xx。响应没有 vector score、combined score 或 rerank 字段。

### 端到端 Answer

```text
POST /v1/knowledge-bases/{knowledge_base_id}/answer
```

响应：

```json
{
  "status": "answered",
  "answer": "候选人在 Starter Agent 项目中实现了…… [cit_01]",
  "claims": [
    {
      "text": "候选人在 Starter Agent 项目中实现了……",
      "citation_ids": ["cit_01"]
    }
  ],
  "citations": [
    {
      "citation_id": "cit_01",
      "document_id": "...",
      "filename": "resume_demo.md",
      "document_version": 2,
      "chunk_id": "...",
      "page": null,
      "section": ["项目经历", "Starter Agent"],
      "start_line": 20,
      "end_line": 27,
      "quote": "……"
    }
  ],
  "refusal_reason": null
}
```

无证据：

```json
{
  "status": "refused",
  "answer": "知识库中没有足够证据回答这个问题。",
  "claims": [],
  "citations": [],
  "refusal_reason": "no_evidence"
}
```

### 引用解析

```text
GET /v1/knowledge-bases/{knowledge_base_id}/citations/{chunk_id}
```

- 活动 Chunk 返回 canonical 定位信息。
- 更新产生且存在同 Scope 墓碑的旧 Chunk 返回 `410 citation_gone`。
- 整份文档硬删除后墓碑也被删除，此后相同 ID 按不可见对象返回 404。
- 所有情况都不得返回旧正文。

### 内部服务接口

```python
retrieval_result = await retrieval_service.retrieve(
    scope=scope,
    knowledge_base_id=kb_id,
    question=question,
    filters=filters,
    top_k=6,
)

answer = await generation_service.generate(
    question=question,
    evidence=retrieval_result.evidence,
    provider=provider,
    model=model,
)
```

Retrieval 测试不构造 Provider。Generation 测试直接传入固定 Evidence，不访问 FTS5。

### Chat 集成

`ChatRequest` 增加：

```json
{
  "knowledge_base_id": "...",
  "knowledge_mode": "off | required"
}
```

- `required`：当前问题走 `KnowledgeApplicationService.answer()`。
- `off`：保持现有聊天逻辑。
- 第一阶段不实现不透明 `auto` 模式。

高级流程未来可以注册只读 `knowledge_retrieve` 工具，但最终私人事实回答仍必须通过 CitationValidator。该工具不能更新、删除或触发外部操作。

## 状态流转与交互流程

### 新文档入库

```text
用户选择 Markdown
  → 勾选授权确认
  → 点击“上传并入库”
  → queued
  → parsing
  → normalizing
  → chunking
  → indexing
  → indexed
```

任一步失败：

```text
当前阶段 → failed(error_code, retryable)
```

失败版本不可检索。重试创建新 Job，并先清理失败版本半成品。

### 文档更新

```text
active v1
  → 上传 v2
  → v2 独立完成 Parse/Normalize/Chunk/Metadata/Text Index
  → v2 失败：v1 继续服务
  → v2 成功：单事务
       v2 设为 active
       v1 从检索撤下
       为旧 chunk_id 写入无正文失效墓碑
       删除 v1 Chunk/FTS/正文
  → 旧引用返回 410
```

激活事务失败时不得出现两个活动版本。

### 文档删除

```text
indexed/failed
  → 用户确认
  → deleting（立即禁止新检索）
  → 同事务删除 FTS、Chunk、版本、任务、墓碑和文档元数据
  → 提交
  → 反向检索验证
  → API 返回 deleted
```

删除验证：

- 按 `document_id` 无记录。
- 按旧 `chunk_id` 无记录。
- FTS5 唯一关键词无命中。
- 相同 Scope 的新 Retrieval 无旧 Chunk。
- 重启应用后仍无命中。

若事务回滚，文档保持不可检索，页面显示 `delete_failed` 并允许安全重试。

### 问答

```text
用户问题
  → FTS5 Retrieval
  → no evidence? ──是──> 明确拒答，不调用模型
  → Evidence Gate
  → insufficient? ──是──> 明确拒答
  → conflict? ──是──> 带双方引用说明冲突
  → Generation
  → Citation validation
  → 通过：展示答案与相邻引用
  → 失败：一次格式修复；仍失败则拒答或返回错误
```

## 错误处理

| 错误码 | HTTP/业务结果 | 界面行为 |
|---|---:|---|
| `upload_authorization_required` | 400 | 要求勾选授权确认 |
| `unsupported_document_type` | 415 | 提示只支持 Markdown |
| `document_too_large` | 413 | 显示配置上限 |
| `document_invalid_encoding` | 422 | 提示转换为 UTF-8 |
| `sensitive_content_detected` | 422 | 阻止入库并显示规则类别 |
| `duplicate_document_content` | 409 | 提示使用已有文档或更新 |
| `parse_failed` | 422/任务失败 | 显示解析失败 |
| `document_no_indexable_content` | 422/任务失败 | 提示无可索引正文 |
| `fts5_unavailable` | 503/启动自检 | 明确提示当前 SQLite 不支持 FTS5 |
| `fts5_trigram_unavailable` | 503/启动自检 | 明确提示 tokenizer 不可用 |
| `index_failed` | 500/任务失败 | 不激活版本 |
| `ingestion_interrupted` | 409/任务失败 | 允许安全重试 |
| `document_version_conflict` | 409 | 刷新后重新更新 |
| `document_not_found` | 404 | 不泄露其他 Scope |
| `citation_gone` | 410 | 提示文档已更新 |
| `no_evidence` | 200/refused | 明确说明无证据 |
| `insufficient_evidence` | 200/refused | 明确说明证据不足 |
| `retrieval_failed` | 500 | 与无命中区分 |
| `generation_invalid_output` | 502 | 不展示未校验回答 |
| `citation_validation_failed` | 200/refused 或 502 | 不展示无依据结论 |
| `external_processing_not_confirmed` | 403 | 要求确认 Generation 数据去向 |
| `delete_failed` | 500 | 保持不可检索并允许重试 |

原始 Provider 响应、文件正文、query、quote 和凭据不进入公开错误。前端轮询失败显示“状态查询失败”，不能把任务误标为成功。

## 性能与安全考虑

### 性能

- 默认限制：单文件 2 MiB、每知识库 100 份活动文档、最多 5,000 个活动 Chunk。
- FTS5 查询先执行 Scope 与元数据过滤，再应用 top-k。
- Chunk 列表和预览使用 cursor 或稳定 ordinal 分页。
- 上传返回 202，解析和索引由后台任务完成。
- 对 1～2 字短查询的 `instr` 补充扫描设置候选上限，不允许无界全库扫描。
- FTS5 索引与 Chunk 在同一 SQLite 数据库，避免跨存储网络和一致性开销。

验收环境目标：

- 2 MiB Markdown 在 10 秒内完成本地 Parse、Chunk 和 Text Index。
- 5,000 个活动 Chunk 下 Retrieval P95 小于 1 秒。

目标需在实际课程设备上复测；不满足时先优化 SQL、索引和分页，再讨论新一阶段检索架构。

### 权限和隐私

- Scope 从可信服务端上下文获得。
- 所有 Store 和 Index 方法要求 Scope。
- 外部 Generation 只发送问题和本次证据，不发送全库。
- 文档正文、Chunk、query、quote、邮箱、凭据和 Provider 原始错误不进入日志。
- 文件名、Markdown、标题和代码块视为不可信输入；前端使用 `textContent`。
- Generation 禁用工具调用，文档提示注入不能触发邮件或写操作。
- 敏感扫描只记录规则 ID。
- SQLite 文件由操作系统限制当前用户读取；更高保护需要单独确认加密需求。

### 查询安全

- FTS5 MATCH 表达式由 QueryNormalizer 生成。
- 用户输入不直接拼接进 SQL 或 MATCH 语法。
- SQL 参数化。
- 同义词表为只读配置，不从上传文档生成。
- 特殊字符、引号、布尔词、通配符和超长 query 有单元测试。

### 删除安全

- 删除开始即撤下可检索标志。
- FTS、Chunk、版本、任务、墓碑和文档记录在同一事务删除。
- 启用 `secure_delete` 并执行 WAL checkpoint 策略。
- 删除后运行反向查询；验证失败不得返回成功。
- 应用不能承诺删除系统备份、磁盘快照或外部 Provider 保留副本；这些范围必须在部署前披露。

### 可观测性

允许记录：

- knowledge base、document、version 和 job ID。
- 内容指纹前缀。
- stage、Chunk 数、耗时、候选数、错误码。
- Query 配置指纹和 Generation Provider/model。

禁止记录：

- 文档正文、Chunk、用户问题、matched term 原文、quote。
- 文件系统私人路径。
- API Key、密码、授权码、邮件正文和第三方联系方式。

## 测试策略

### 单元测试

1. `UploadValidator`：扩展名、UTF-8、NUL、大小、路径字符和二进制伪装。
2. `SensitiveContentScanner`：模拟 API Key、密码、授权码和证件模式被阻止，日志不含命中值。
3. `MarkdownParser`：标题路径、列表、表格、代码围栏和原始行号。
4. `Chunker`：固定输入产生稳定 Chunk，覆盖重叠、超长段落和空文档。
5. `SQLiteFtsIndex`：建表、自检、写入、查询和删除。
6. `QueryNormalizer`：中文、英文、引号、特殊字符、短查询和显式同义词。
7. `FtsRetriever`：Scope、metadata filter、BM25 升序、确定性排序和 top-k。
8. `EvidenceSufficiencyGate`：无命中、覆盖不足、数字缺失和冲突。
9. `CitationAssembler`：文档、版本、Chunk、section、行号和 quote 来自 canonical 数据。
10. `CitationValidator`：拒绝不存在、非本次命中、错误版本和 quote 不匹配。
11. `RagGenerationService`：固定 Evidence、空 Evidence、提示注入和无工具调用。

### Store 与生命周期测试

1. 新版本成功后原子替换旧版本，旧 Chunk、FTS 和正文清除。
2. 新版本解析、Chunk 或索引失败时旧版本继续可检索。
3. 删除失败时保持不可检索并可幂等重试。
4. 删除后按 document ID、chunk ID、唯一关键词均无命中。
5. SQLite 重开连接和应用重启后删除仍成立。
6. 扫描数据库、WAL 和日志中的唯一测试标记。
7. 两个 Scope 相互不可见、不可预览、不可检索、不可更新、不可删除。

### Retrieval 独立评测

使用两份固定安全 Markdown：

- 虚构候选人简历。
- 虚构岗位 JD 或面试准备笔记。

固定：

- Chunk 参数。
- FTS5 tokenizer 和同义词表版本。
- 查询集与相关 Chunk 标注。
- top-k 和排序规则。

指标：

- Recall@K。
- MRR。
- 无证据查询空命中率。
- Scope 过滤正确率。
- 删除后残留命中数。

Retrieval 评测不初始化或调用任何 Provider。

### Generation 独立评测

Generation 直接使用固定 Evidence：

1. 单一证据支持一个事实。
2. 多个事实需要多个引用。
3. 空 Evidence 或无关 Evidence 必须拒答。
4. 部分问题有证据时只回答有证据部分。
5. 冲突 Evidence 引用双方。
6. 提示注入 Evidence 不执行指令。
7. 伪造 evidence ID 校验失败。
8. 正确结论但无引用仍不得通过。

Generation 评测不访问 FTS5。

### API 与集成测试

1. 前端入口可直接进入知识库。
2. Multipart 上传返回 202，刷新后状态可查询。
3. 上传、解析、Chunk 和索引失败显示不同状态。
4. 两份文档成功索引，列表 Chunk 数和预览正确。
5. `/retrieve` 不调用 Generation。
6. `/answer` 有证据时返回可定位引用，无证据时拒答。
7. 更新使用 If-Match，冲突返回 409。
8. 删除后列表、预览、Retrieval、Answer 和旧引用均不可使用。
9. 外部 Generation 未确认时返回 403。
10. 岗位、简历和邮件工具结果不自动入库或触发副作用。

### 最终验证

最终验证必须在 Task 全部通过后单独执行：

```powershell
uv run pytest -q
```

并按 `docs/rag-acceptance.md` 完成人工验收。通过依据必须包括：

- 可访问的知识库入口。
- 两份安全 Markdown 成功索引。
- Chunk 数与预览正确。
- 有证据回答与可定位引用。
- 无证据稳定拒答。
- 更新后旧 Chunk 失效。
- 删除后重启仍不可检索。
- Retrieval 与 Generation 独立测试。
- 日志与测试产物无真实秘密和不必要正文。

“回答流畅”“回答看起来合理”或“模型自称已检索”不能作为通过依据。

## 风险与待确认事项

### 已识别风险

- **词法召回风险：** 同义表达可能无法命中；通过显式领域同义词、短语匹配和固定评测集缓解。
- **trigram 短查询风险：** 1～2 字查询召回有限；使用受 Scope 和候选上限约束的确定性补充查询。
- **FTS5 环境风险：** Python/SQLite 构建可能缺少 FTS5 或 trigram；启动自检失败即阻止启用。
- **BM25 误用风险：** SQLite FTS5 更相关分数更小；测试必须验证升序语义。
- **权限风险：** FTS 查询若未与关系表 Scope 过滤绑定可能越权；所有索引 API 强制 Scope。
- **提示注入风险：** Evidence 数据边界、Generation 禁用工具和 CitationValidator。
- **引用支持不足：** 词法相关不等于支持结论；使用 Evidence Gate 和 claim-to-evidence 校验。
- **版本残留风险：** 新版本先建后切换，旧 FTS 与 Chunk 在事务中清理。
- **删除残留风险：** 使用硬删除、secure_delete、WAL checkpoint 和删除后反向验证。
- **日志泄露风险：** 使用知识库审计字段白名单和唯一测试标记扫描。
- **后台任务风险：** 第一阶段只支持单进程 worker；多进程前需新增可靠队列。

### 实施前待确认

1. 部署是否严格保持单进程；若使用多个 Uvicorn worker，必须先引入可靠任务队列。
2. 目标 Python 3.11/3.12 环境的 SQLite 是否均支持 FTS5 `trigram`。
3. 允许的外部 Generation Provider、地区、保留政策和披露文案。
4. 2 MiB、100 文档、5,000 Chunk 的默认容量是否符合目标设备。
5. Retrieval 评测集、top-k、标题/短语权重、同义词表和 Evidence Gate 规则。
6. 系统备份、云同步或磁盘快照是否存在，以及硬删除覆盖范围。
7. 是否要求 SQLite 静态加密；当前只依赖操作系统文件权限。
8. 旧聊天中的引用在文档更新或删除后如何标记失效。
9. 后续公开岗位或邮件摘要是否需要显式保存流程；第一阶段不自动导入。

本设计与 `rag-requirements.md`、`rag-task.md` 共同构成实施基线。只有用户确认三份文档统一后，才按 `rag-task.md` 的 Task 顺序执行；全部 Task 通过后再进入最终验证。
