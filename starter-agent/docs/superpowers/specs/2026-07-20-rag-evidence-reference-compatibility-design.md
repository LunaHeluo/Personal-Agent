# RAG 逐证据引用兼容设计

## 背景与问题

当前 `GeneratedClaim` 使用 `evidence_ids: list[str]` 和单个 `quote: str`。
`assemble_citations()` 会用同一个 quote 逐一校验所有 Evidence。简历与 JD 的匹配
结论天然需要跨文档证据，而两份文档通常不包含完全相同的连续原文，因此模型输出
多个 Evidence ID 时容易触发 `citation_validation_failed`。

本设计只修复 Generation 到 canonical Citation 的结构表达，不改变 Retrieval、
Chunk、metadata filter、拒答门槛、文档生命周期或 FTS5/BM25 方案。

## 设计目标

1. 一个 claim 可以为每个 Evidence 提供独立的连续原文 quote。
2. 旧的 `evidence_ids + quote` 单证据格式继续可用。
3. 不通过模糊匹配、去标点或模型二次判断放宽引用校验。
4. 文件名、版本、行号、Chunk ID 等 canonical metadata 继续只由服务端生成。
5. `/answer` 的既有调用方仍能读取 `text`、`evidence_ids` 和 `quote`。

## 方案选择

采用“双格式输入、统一内部引用、兼容输出”：

- 新格式使用 `evidence_refs`，每项包含 `evidence_id` 和 `quote`。
- 旧格式仍接受 `evidence_ids` 和 `quote`。
- 解析后统一得到有序的逐证据引用列表，再交给 canonical Citation 组装器。

不采用仅强化提示词的方案，因为模型仍可能输出多个 Evidence ID。也不直接删除旧
字段，以免破坏 `/answer` 的现有消费者和测试。

## 数据模型

新增模型：

```python
class GeneratedEvidenceRef(BaseModel):
    evidence_id: str
    quote: str
```

`GeneratedClaim` 扩展为：

```python
class GeneratedClaim(BaseModel):
    text: str
    evidence_refs: list[GeneratedEvidenceRef] = []
    evidence_ids: list[str] = []
    quote: str | None = None
```

模型的 `before` validator 先根据原始输入键执行以下归一化规则，再完成字段类型
验证：

1. 新格式必须至少包含一个 `evidence_ref`。
2. 新格式与旧格式不能在同一个 claim 中同时出现。
3. 新格式中的 Evidence ID 不能重复，quote 去除首尾空白后不能为空。
4. 旧格式必须同时提供非空 `evidence_ids` 和非空 `quote`。
5. 旧格式保留原语义：同一个 quote 必须逐字存在于每个声明的 Evidence。
6. 新格式通过校验后，由 validator 填充兼容字段；旧格式通过校验后，由 validator
   生成逐证据引用列表。
7. 内部统一暴露按输入顺序排列的 `(evidence_id, quote)` 引用对。

为了保持 API 兼容，新格式 claim 序列化时同时返回：

- 完整的 `evidence_refs`；
- `evidence_ids`，值为所有逐证据引用的 ID；
- `quote`，值为第一条逐证据引用的 quote，并标记为兼容字段。

新调用方必须读取 `evidence_refs`；旧调用方仍能读取类型不变的
`evidence_ids: list[str]` 和 `quote: str`。top-level `citations` 始终包含每条
完整 canonical 引用，是展示和定位来源的权威字段。

## Generation 协议

系统提示要求模型优先输出：

```json
{
  "status": "answered",
  "answer": "候选人的 Python 能力符合岗位要求。",
  "claims": [
    {
      "text": "候选人的 Python 能力符合岗位要求。",
      "evidence_refs": [
        {"evidence_id": "E1", "quote": "简历中的连续原文"},
        {"evidence_id": "E2", "quote": "JD 中的连续原文"}
      ]
    }
  ]
}
```

提示必须明确：

- 每个引用项只对应一个 Evidence ID 和该 Evidence 内的一段连续原文；
- 不得改写 quote；
- 不得输出文件名、行号或 Chunk ID；
- 无证据时使用现有拒答协议；
- 只输出裸 JSON 或现有规则允许的单层 JSON code fence。

解析器继续接受旧模型的单证据 `evidence_ids + quote` 输出。

## 引用组装与错误处理

`assemble_citations()` 遍历归一化后的逐证据引用对：

1. Evidence ID 必须存在于本次允许的 Evidence 集合。
2. quote 必须是对应 `Evidence.text` 的非空连续子串。
3. 每个合法引用生成一条 canonical `Citation`。
4. canonical 文件名、版本、章节、行号和 Chunk ID 全部来自 Evidence。

以下证据对应错误返回 `citation_validation_failed`：

- 未知 Evidence ID；
- quote 不是对应 Chunk 的连续原文；
- claim 没有任何有效引用。

空 quote、重复 Evidence ID、新旧格式混用，以及其他 JSON、字段类型或结构错误
返回 `generation_invalid_output`。服务端不在错误响应或日志中记录完整私人正文
或模型 quote。

## API 兼容性

`/v1/knowledge-bases/{knowledge_base_id}/answer` 的 top-level 字段保持不变：

- `status`
- `answer`
- `claims`
- `citations`
- `refusal_reason`

`claims` 新增 `evidence_refs`，并保留原有 `text`、`evidence_ids`、`quote`。
`citations` 的结构和 canonical 语义不变。前端现有引用展示继续读取 top-level
`citations`，不依赖模型提供的 metadata。

## 测试策略

### 单元测试

1. 两个 Evidence 使用不同 quote 的新格式 claim 生成两条 canonical Citation。
2. 旧单证据格式继续生成相同 Citation。
3. 旧多 Evidence 共用 quote 时，只有 quote 存在于全部 Evidence 才通过。
4. 新格式的未知 ID、改写 quote、空 quote、重复 ID 和新旧混用均失败。
5. Generation 提示包含逐证据引用协议。
6. 裸 JSON、单层 fenced JSON 和严格状态枚举行为保持不变。

### 集成测试

1. 简历与 JD 的跨文档回答可返回不同 quote，并通过 `/answer`。
2. API claim 同时包含兼容字段和完整 `evidence_refs`。
3. top-level citations 分别定位到简历和 JD 的 canonical Chunk。
4. 无证据拒答、更新失效和删除不可检索测试保持通过。

### 真实模型验证

只使用仓库内无敏感信息的 `resume_demo.md` 与 `job_demo.md`，调用现有
`zhipu / glm-4.7`。必须检查：

- Retrieval 同时覆盖 `resume` 与 `job_description`；
- Generation 返回 `answered`；
- 每个 Evidence 引用有独立 quote；
- 每条 quote 可在对应 canonical Chunk 中逐字定位；
- 无证据问题仍返回 `refused/no_evidence`；
- 不记录 API Key 或不必要的全文。

## 风险与边界

- 兼容字段 `quote` 在多证据 claim 中只能表示第一条引用，新客户端必须使用
  `evidence_refs` 或 top-level `citations`。README 和验收文档需要明确这一点。
- 真实模型仍可能输出旧的多 Evidence 单 quote 格式；服务端保持严格拒绝，不猜测
  quote 与 Evidence 的对应关系。
- 本设计不改变证据充分性判断。检索结果存在不等于模型可以提出资料外结论。
- 本设计不新增重试或自动修复模型输出，避免额外模型调用和不可审计的引用重写。

## 验收标准

1. 新格式跨简历/JD claim 能返回两条可定位 canonical Citation。
2. 旧单证据格式的现有测试和 API 输出保持兼容。
3. 未知 ID、非连续 quote、重复 ID、空引用和混合格式全部被拒绝。
4. Retrieval、拒答、更新、删除和 fenced JSON 回归全部通过。
5. 完整 pytest 退出码为 0。
6. 真实 `glm-4.7` 使用安全 fixture 完成一次跨文档回答和一次无证据拒答。
