# RAG 中文查询与 GLM 结构化输出加固设计

## 背景与目标

当前知识库已正确读取并索引 JD 与简历，但自然问句“我的简历匹配哪个岗位”被当成
一个完整 FTS 短语，导致零命中并在 Generation 前返回 `no_evidence`。同时，真实
`glm-4.7` 可能把合法 JSON 包在 Markdown `json` code fence 中，当前 Generation
只接受裸 JSON，因而返回 `generation_invalid_output`。

本次修复目标：

1. 在不引入 Embedding、向量数据库或模型 Rerank 的前提下，让常见中文求职问句
   能稳定召回相关简历和 JD Chunk。
2. 对简历与岗位比较问题，同时提供两类文档的证据，避免只命中一侧。
3. 兼容 `glm-4.7` 的单层 JSON code fence，同时保持严格 Schema、Evidence ID、
   quote 连续子串和 canonical 引用校验。
4. 保持无证据拒答、Scope/metadata filter、工具隔离和删除语义不变。

## 方案比较与决策

### 方案 A：确定性领域词抽取与受限结果合并（采用）

- 从连续中文问句中提取版本化的求职领域短语，例如“简历”“岗位”“经历”“技能”
  “职责”“要求”“项目”“匹配”。
- ASCII/数字及原有显式同义词继续走 FTS5 长词查询。
- 长词 FTS 结果与受限短词查询结果合并，按稳定顺序去重并裁剪到 top-k。
- 识别“简历 + 匹配/适合 + 岗位/JD”比较意图；在授权范围内分别为 `resume` 和
  `job_description` 保留至少一个候选 Chunk，然后再按稳定排序补足 top-k。

优点是确定性、无需网络或新依赖，并保持第一阶段技术边界。缺点是领域词表需要
显式维护，不能覆盖所有开放领域语义。

### 方案 B：通用中文二元/三元切分

召回范围更广，但会产生大量泛化片段和噪声，容易让无关 Chunk 通过 Evidence Gate，
也难以解释排序。本阶段不采用。

### 方案 C：Embedding 或模型查询改写

语义召回更强，但改变已确认的第一阶段架构，引入外部数据处理、模型依赖和新的删除
验证范围。本次不采用。

## Retrieval 设计

### QueryNormalizer

`NormalizedQuery` 扩展为能够表达：

- `terms`：全部规范化词项。
- `match_expression`：长度至少为 3 的 FTS5 词项。
- `short_terms`：1–2 字符或领域短语的受限回退词项。
- `comparison_intent`：是否为简历与岗位比较。

处理顺序：

1. 清理 NUL 和重复空白。
2. 提取 ASCII/数字/显式分隔词。
3. 在每段连续中文文本中按最长优先提取版本化领域短语。
4. 应用现有显式同义词表并稳定去重。
5. 构造 FTS5 表达式和短词集合。
6. 由确定性规则识别比较意图，不调用模型。

完整中文句子不再整体作为唯一 FTS 短语；只有显式引用或已分隔的具体长词才进入
精确长词检索。

### KnowledgeRetriever

检索分两路：

1. 有 `match_expression` 时执行 FTS5/BM25 查询。
2. 有 `short_terms` 时执行受 Scope、知识库和 metadata filter 约束的短词查询。
   多个受限领域短词按 OR 召回，并按命中词数量降序、文档 ID、Chunk ordinal
   稳定排序；不能沿用当前“所有短词必须同时存在于同一 Chunk”的 AND 语义。

结果按以下键稳定合并：

1. FTS5 命中优先，保留 BM25 顺序。
2. 已出现的 `chunk_id` 去重。
3. 短词补充结果按 Store 的稳定顺序追加。
4. 比较意图且 `top_k >= 2` 时，如果请求 filter 允许，确保 `resume` 与
   `job_description` 各有证据；不得为满足覆盖而绕过用户提供的
   document/type/version filter。
5. 最终裁剪到 `top_k` 并重新生成连续 `rank`。

如果一类文档不存在或 filter 排除了该类型，不伪造证据；后续 Evidence Gate 可拒答
或只回答有证据部分。

## Generation 设计

在 `_GeneratedPayload.model_validate_json()` 前增加一个私有、纯函数式 JSON
envelope normalizer：

- 去除首尾空白。
- 裸 JSON 原样返回。
- 只接受完整包裹内容的单层 ` ```json ... ``` ` 或 ` ``` ... ``` `。
- code fence 前后出现解释文字、存在多个 fence、内部不是 JSON 时不做宽松提取，
  继续返回 `generation_invalid_output`。
- 不使用正则从任意自由文本中“寻找看起来像 JSON 的片段”。

剥离 envelope 后仍执行当前 Pydantic Schema 校验、允许状态校验、Evidence ID 校验、
quote 连续子串校验和 canonical Citation 组装。该兼容层不能降低证据约束。

## 错误处理与安全边界

- Query 为空仍返回 `knowledge_query_invalid`。
- Retrieval 零命中仍在调用 Provider 前返回 `refused/no_evidence`。
- 比较意图只有一侧证据时，不把单侧资料推断成完整匹配结论。
- metadata filter 与 Scope 继续下推到 SQL/FTS 查询，不在结果合并后补做权限隐藏。
- Generation 的任意自由文本、额外说明、无效 JSON、未知 Evidence ID 或不连续 quote
  仍返回稳定错误，不回退为自由回答。
- 文档内容不写入普通日志，新增诊断只允许记录词项数量、命中数量和错误码。

## 测试设计

严格采用 TDD，每项先观察失败再实现。

### Query 与 Retrieval

1. “我的简历匹配哪个岗位”不再成为单个完整 FTS 短语。
2. 该问题能提取“简历 / 匹配 / 岗位”并标记比较意图。
3. 使用一份简历和一份 JD 的固定 fixture 时，Retrieval 同时返回两种
   `document_type`。
4. 同时包含 `AI Agent` 与中文短词时，长词和短词结果都会参与，且 `chunk_id`
   去重、rank 连续、结果不超过 top-k。
5. 多个短词按 OR 召回，并优先返回命中词数量更多的 Chunk。
6. `document_types=["resume"]` 时，比较意图不得补入 JD。
7. `top_k=1` 时不得为了文档类型覆盖返回超过一个结果。
8. 无相关领域词和无内容命中时仍返回空结果。

### Generation

1. 裸 JSON 保持通过。
2. ` ```json ... ``` ` 与普通单层 code fence 通过。
3. fence 外有解释文字、多个 fence、自由文本和无效 JSON 均失败。
4. fenced JSON 中的未知 Evidence ID 或非连续 quote 仍失败。

### 回归与真实链路

1. 运行 Query、Retrieval、Generation、拒答和 Chat 知识库路由专项测试。
2. 运行完整 pytest。
3. 使用安全 fixture 和真实 `glm-4.7` 复验：
   - “我的简历匹配哪个岗位”产生简历与 JD Retrieval Evidence。
   - 有证据回答可解析且引用可定位。
   - 无证据问题仍在 Provider 前拒答。

## 验收标准

- 原问题不再因整句精确匹配而零命中。
- 比较问题在 filter 允许时同时包含简历与 JD 证据。
- 不增加 Embedding、向量索引、模型查询改写或模型 Rerank。
- 真实 `glm-4.7` 的单层 fenced JSON 可以解析，引用校验规则不变。
- 自由文本或引用不合法时不能因兼容层而被接受。
- 专项测试、完整回归和真实模型安全 fixture 验证均通过。
