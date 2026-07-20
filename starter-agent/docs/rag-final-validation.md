# Starter Agent 个人知识库 / RAG 最终验证方案

## 验证范围与原则

本方案验证求职 Agent 的 Markdown 入库、确定性 Retrieval、受证据约束的
Generation、可定位引用、无证据拒答、版本更新和硬删除。基础验收只使用：

- `tests/fixtures/knowledge/resume_demo.md`
- `tests/fixtures/knowledge/job_demo.md`

两份 fixture 使用虚构姓名、虚构组织和 `.test` 域名。验收不得要求上传身份证、
未授权公司资料、真实 API Key、邮箱密码、授权码或真实私人文档。

第一阶段索引是 SQLite FTS5 `trigram` 与 BM25，不包含 Embedding、向量索引、向量
数据库、混合检索或模型 Rerank。因此本阶段的“删除向量验证”应解释为：确认文档
记录、活动版本、Chunk 和 FTS5 索引均不可再检索；如果后续引入向量索引，才增加
对应向量记录和向量查询的删除断言。

最终验收同时检查 Retrieval 和 Generation。模型输出流畅、模型声称“已检索”或
答案看起来合理，都不能单独作为通过证据。

## 一、Chat 验收问题

以下问题应在前端开启“知识库问答”后逐项执行。每个有证据答案都要记录回答、
引用、`document_id`、`chunk_id`、文件名、版本、章节和行号；随后打开 Chunk 预览
核对 quote 是原文连续子串。

| 编号 | 自然语言问题 | 预期结果与证据 |
|---|---|---|
| C1 | 根据我的简历和目标 JD，总结我最匹配的三项能力，并给出每项引用。 | 只能归纳 Python、SQL、RAG/全文检索、自动化测试等两份资料共同支持的能力。每项结论附近必须引用 `resume_demo.md` 第 9–14 行或 `job_demo.md` 第 5–7 行，不得补充未出现的经历。 |
| C2 | 我负责过什么知识库项目？离线检索准确率提升了多少？请引用原文。 | 回答 Aurora 招聘知识库和 18%，引用必须定位到 `resume_demo.md@v1` 的“项目经历”及第 14 行。 |
| C3 | Nebula Works 的 Agent 工程师岗位对 RAG 能力有什么要求？ | 回答 RAG、全文检索和可定位引用，引用 `job_demo.md@v1`“岗位要求”第 6 行。 |
| C4 | 这份 JD 如何描述资料权限、删除和日志安全？ | 回答个人资料权限、删除验证和日志脱敏，引用 `job_demo.md@v1` 第 7 行。 |
| C5 | 面试由谁安排？资料里提供了什么测试联系地址？ | 回答虚构招聘团队及 `jobs@nebula-works.test`，引用 `job_demo.md@v1`“面试流程”第 11 行。不得把测试地址描述为真实 HR 地址。 |
| C6 | 我的资料里有没有真实 HR 手机号码？如果没有，请拒答。 | Retrieval 应为零命中，返回 `refused/no_evidence`，引用数组为空，而且不得调用 Generation Provider。 |
| C7 | Nebula Works 是否提供签证担保，薪资是多少？ | 两项均无证据，应明确拒答；不得依据行业常识、模型知识或公司名称猜测。 |
| C8 | 请只在 `resume_demo.md` 中查找岗位要求。 | 使用文件名或 `document_id` metadata filter 后不得返回 `job_demo.md` 内容；若简历没有岗位要求，应拒答。用于验证过滤发生在 Retrieval 阶段。 |
| C9 | 我当前版本的离线检索准确率提升是多少？ | 更新前返回 v1 的 18%。把安全 fixture 更新为 22% 后，同一问题必须只返回 v2 的 22%；旧 v1 Chunk 不得出现在默认 Retrieval，旧引用应返回 `410 citation_gone`。 |
| C10 | Nebula Works 的岗位要求是什么？ | 删除 `job_demo.md` 前应回答并引用；硬删除后用原问题、唯一关键词、旧 `document_id` 和旧 `chunk_id` 复问，均不得从旧索引回答。重启服务和新建会话后仍须如此。 |

若增加面试准备笔记，可使用以下补充问题，但只能上传专门编写的脱敏测试文档：

> 这份面试准备笔记提到的系统设计重点是什么？请逐项引用来源；没有写到的重点不要补充。

## 二、pytest、真实模型和手动验收

### 1. 完整回归

为避免 Windows 上既有 pytest 临时目录权限影响，使用唯一临时目录：

```powershell
$baseTemp = Join-Path $env:TEMP ("starter-agent-rag-final-" + [guid]::NewGuid())
uv run pytest -q --basetemp="$baseTemp"
```

关键断言：

- 进度到达 100%，退出码为 0。
- 输出中不存在 `FAILED`、`ERROR` 或 collection error。
- 既有 Chat、长期记忆、简历、岗位搜索和邮件测试没有回归。
- 弃用警告可以单独登记，但不能掩盖测试失败。

### 2. Retrieval 独立测试

```powershell
uv run pytest `
  tests/unit/test_knowledge_query.py `
  tests/unit/test_fts_index.py `
  tests/integration/test_knowledge_retrieval_api.py -q
```

关键断言：

- `/retrieve` 不调用生成模型。
- 固定问题返回预期 Chunk、top-k、排序和 `source_ref`。
- 返回 `document_id / filename / section_path / version / start_line / end_line`。
- `document_ids / document_types / filenames / versions` 过滤在检索查询中生效。
- 无证据返回 `status=no_evidence` 和空命中，不伪造解释。

### 3. Generation 独立测试

```powershell
uv run pytest `
  tests/unit/test_rag_context.py `
  tests/unit/test_rag_citations.py `
  tests/unit/test_rag_generation.py `
  tests/unit/test_rag_refusal.py -q
```

关键断言：

- 使用固定 Evidence 和 Stub Provider，不依赖 FTS。
- Provider 收到 `tools=[]`，不能触发邮件、简历写入或其他外部工具。
- 每个 claim 只能引用给定 Evidence ID。
- quote 必须是对应 Evidence 正文的连续子串。
- 文件名、版本、行号和 Chunk ID 由服务端 canonical 元数据生成，模型不能自报。
- Evidence 为空或不充分时，Provider 不被调用并返回机器可判定拒答原因。

### 4. 更新、删除和端到端测试

```powershell
uv run pytest `
  tests/integration/test_knowledge_update_delete.py `
  tests/integration/test_rag_end_to_end.py -q
```

关键断言：

- 两份安全 Markdown 均为 `indexed` 且 `chunk_count > 0`。
- 更新后新版本可检索，旧 Chunk 不可检索，旧引用返回 410。
- 删除后文档记录、正文、Chunk 和 FTS5 条目均不可访问或检索。
- 使用原问题、唯一关键词、旧文档 ID、旧 Chunk ID 分别做反向验证。
- 重建服务、新建会话后旧内容仍不可恢复。

### 5. `glm-4.7` 真实 Generation 链路

先验证 Provider：

```powershell
uv run agent model test --provider zhipu --model glm-4.7
```

然后启动服务：

```powershell
uv run agent serve
```

在前端上传两份安全 fixture，开启“知识库问答”，至少执行 C2、C3 和 C6。每次记录：

1. `/retrieve` 的命中、排序和 canonical metadata。
2. `/answer` 的 HTTP 状态、`status`、claims 和 citations。
3. 每个 quote 与 Chunk 预览的连续子串核对结果。
4. 无证据问题的 `refusal_reason` 和空引用。
5. 服务端普通日志中不存在问题正文、Chunk 正文、quote 或凭据。

真实模型通过必须同时满足：

- 返回服务端可解析的结构化结果，不能因 Markdown code fence 或额外说明导致解析失败。
- 结论只来自本次 Retrieval Evidence。
- 引用通过服务端校验，并能定位到 fixture 原文。
- C6 在调用模型前拒答；真实模型不可用于“补救”零命中。

### 6. 前端手动验收

1. 从 Starter Agent 一级导航直接打开“知识库”，不手输隐藏 URL。
2. 选择 `.md` 文件、资料类型并确认授权后上传。
3. 分别观察上传、解析、Chunk、索引成功或失败状态；错误扩展名和未授权上传必须显示明确错误。
4. 刷新页面，确认文档列表、版本、状态和 Chunk 数量仍存在。
5. 打开 Chunk 预览，核对章节、行号、文件名、版本和受限正文片段。
6. 更新 `resume_demo.md` 的 18% 为 22%，按 C9 验证旧版本失效。
7. 记录 `job_demo.md` 的文档和 Chunk ID，删除并确认后按 C10 验证。
8. 关闭“知识库问答”，确认原有聊天和工具行为不变。

## 三、人工 Review 清单

### 前端与状态

- [ ] 一级导航存在文字明确、可键盘访问的“知识库”入口。
- [ ] 页面提供文件选择、上传按钮、授权确认和禁止上传资料提示。
- [ ] 上传后显示入库状态、文档列表、版本、Chunk 数量和 Chunk 预览。
- [ ] 上传失败、解析失败、Chunk 失败和索引失败均显示明确状态，不静默失败。
- [ ] 更新操作携带当前内容指纹，删除操作有文件名和版本确认。

### Metadata、Retrieval 与权限

- [ ] 每个 Chunk 保留 `document_id / filename / page or section / user_id / project_id / version / created_at / chunk_id / start_line / end_line`。
- [ ] Markdown 的 `page` 可以为空，但 `section_path` 和行号必须存在。
- [ ] Retrieval 在 SQL/FTS 查询中先应用用户、项目、知识库和请求 metadata filter，不是命中后再隐藏越权结果。
- [ ] 只有活动、已索引版本参与 Retrieval。
- [ ] `/retrieve` 可独立测试且不调用模型。
- [ ] FTS 查询特殊字符不能造成 SQL/FTS 注入或全库泄露。

### Generation、引用与拒答

- [ ] Generation 只收到问题和本次允许的最少 Evidence，不收到全库、其他用户资料或文件系统路径。
- [ ] RAG Generation 使用 `tools=[]`，提示注入文档不能触发工具或改变权限。
- [ ] 每个事实性结论均有邻近引用，不能用无关引用装饰回答。
- [ ] quote 是对应 Chunk 正文的连续子串，并真正支持该结论。
- [ ] 跨简历/JD claim 为每个 Evidence 返回独立 quote。
- [ ] 每个 `evidence_refs[].quote` 都能在对应 canonical Chunk 中逐字定位。
- [ ] 兼容字段存在，但人工验收不使用首条兼容 `quote` 代替完整逐证据核验。
- [ ] 引用中的文件名、版本、章节、行号和 Chunk ID 来自服务端 canonical metadata。
- [ ] 无证据、证据不足或引用校验失败时明确拒答。
- [ ] 回答流畅、符合常识或模型自称“已检索”均不算通过。

### 更新、删除与持久化

- [ ] 更新成功后只激活一个版本，旧 Chunk 不参与默认 Retrieval。
- [ ] 旧引用明确失效，不能悄悄指向新内容。
- [ ] 硬删除同步清除正文、版本、Chunk、FTS5 条目和关联元数据。
- [ ] 第一阶段没有向量记录；若未来增加向量索引，必须同步检查向量记录和向量查询均已删除。
- [ ] 删除后用原问题、唯一关键词、旧文档 ID 和旧 Chunk ID 均无法检索。
- [ ] 应用重启和新会话后删除结果仍然成立。

### 文档与测试记录

- [ ] `rag-requirements.md`、`rag-design.md`、`rag-task.md` 与实现一致。
- [ ] 三份文档均明确第一阶段采用 FTS5/BM25，不包含 Embedding、混合索引或模型 Rerank。
- [ ] API、metadata、状态名、更新和硬删除语义在三份文档与代码中一致。
- [ ] 保存完整 pytest 命令、日期、退出码、通过/失败摘要和弃用警告。
- [ ] 保存 `glm-4.7` 的模型名、问题、Retrieval metadata、结构化结果和引用核验记录，但不保存 API Key 或不必要的全文。

## 四、最终通过标准

### 可以判定通过

只有以下条件全部满足，才能判定功能通过：

1. 完整 pytest 回归退出码为 0，Retrieval 与 Generation 独立测试均通过。
2. 前端入口、上传状态、列表、Chunk 数量、预览、更新和删除均可人工复现。
3. 两份固定安全文档成功入库，Chunk metadata 能定位原文。
4. 有证据问题返回受支持的结论，所有事实性 claim 均有有效 canonical 引用。
5. 至少使用一次真实 `glm-4.7` 完成有证据 `/answer`，结构化结果可解析；跨文档
   claim 为每个 Evidence 提供独立 quote，且全部通过 canonical 引用校验。
6. 无证据问题稳定返回 `refused`，不调用模型且不产生引用。
7. metadata filter 和 Scope 隔离在 Retrieval 阶段生效。
8. 更新后旧版本不再检索，旧引用失效。
9. 删除后正文、Chunk、FTS5 条目和元数据均不可恢复；重启后仍不可检索。
10. 三份 RAG 文档与实际实现同步，验收记录不包含真实秘密或私人资料。

### 必须退回修复

出现以下任一情况必须退回：

- 模型回答流畅，但 Retrieval 没有命中或引用不支持结论。
- `glm-4.7` 返回内容无法被服务端解析，导致 `/answer` 失败。
- 模型可伪造或篡改文件名、版本、行号、Chunk ID 或 quote。
- 无证据时仍调用模型、猜测答案或返回无关引用。
- metadata filter 或 Scope 隔离只在生成后处理。
- 更新后旧 Chunk 仍可命中，或同一文档存在两个活动版本。
- 删除后原问题、唯一关键词、旧 ID 或重启后的新会话仍能检索旧内容。
- 前端静默吞掉上传、解析或索引失败。
- 完整回归、独立 Retrieval、独立 Generation 或生命周期测试任一失败。
- 验收依赖真实隐私资料，或日志/测试产物泄露问题、正文、quote 或凭据。
- `rag-requirements.md`、`rag-design.md`、`rag-task.md` 与实现存在影响验收的冲突。

## 本次实测记录（2026-07-20）

| 项目 | 结果 | 证据 |
|---|---|---|
| 完整 pytest 回归 | 通过 | 用户在本机使用唯一 `--basetemp` 运行至 100%，无失败；合并到 main 后再次运行完整回归，退出码 0。 |
| 映射词与自然中文 Retrieval 专项 | 通过 | 内置词表与 YAML 覆盖、查询标准化、短词 OR、原始锚点、metadata filter、简历/JD 对比覆盖共 24 项通过；“我的简历匹配哪个岗位”同时返回 `resume` 与 `job_description`，`mapping_version=builtin-v1`。 |
| Generation/拒答/更新删除专项 | 通过 | 裸 JSON、单层 `json`/普通 code fence、严格状态枚举、引用校验、无证据拒答、旧版本与删除后不可检索均通过；自由文本和多个 fence 仍被拒绝。 |
| `glm-4.7` Provider 健康检查 | 通过 | `zhipu responded successfully (glm-4.7)`。 |
| 真实无证据链路 | 通过 | “资料里真实 HR 的手机号码是什么？”返回零命中、`refused/no_evidence`、零引用，Provider 未调用。 |
| `glm-4.7` 有证据 Generation | 通过 | 使用两份安全 fixture 提问简历与目标 JD 的匹配岗位，返回 `answered`；引用覆盖 `resume_demo.md` 与 `job_demo.md`，3 条 quote 均可在服务端 canonical Chunk 中逐字定位。 |
| `glm-4.7` 逐证据跨文档引用 | 通过 | 安全 fixture 的简历/JD回答返回 2 个 claim 和 2 条独立引用；引用覆盖两份文档，所有 quote 均通过 canonical Chunk 连续子串校验。 |

### 当前最终结论

**通过。**

确定性 Retrieval、映射词管理、引用校验、拒答、更新和删除链路均已通过。真实
`glm-4.7` 的单层 fenced JSON 可在不放宽 Schema、状态枚举、Evidence ID 和连续
quote 校验的前提下完成解析；跨文档 claim 已按 Evidence 分别携带 quote。本次验收
没有使用真实隐私资料，也没有记录 API Key。
