# RAG 结构化输出最小重试设计

## 背景

当前 RAG Retrieval 已能从知识库找到简历和岗位 JD，但 `RagGenerator` 依赖模型
严格遵守 JSON 协议。模型偶尔返回普通文本、额外说明、非法状态或不完整字段时，
系统会返回 `generation_invalid_output`：

> 模型没有返回可验证的结构化结果

此前已设计 Provider 通用原生 Schema 能力，但该方案涉及 Provider 接口、模型能力
回退、通用解析器和多类测试。当前优先目标是以最少改动让“根据知识库简历匹配
岗位”能够稳定完成，因此本设计暂时取代通用 Provider 方案作为当前实现范围。
通用方案保留，当前不执行。

## 目标

1. 只修改 RAG Generation，不修改 Provider 通用接口。
2. 首次结构化结果无效时，使用同一模型重新生成一次。
3. 第二次生成使用更明确的 JSON 协议和完整输出示例。
4. 简历与 JD 的证据必须分别提供 Evidence ID 和逐字 quote。
5. 第二次仍无效时停止，不发生第三次模型调用。
6. 保持现有 Evidence ID、连续 quote 和 canonical Citation 严格校验。
7. 普通 Chat、工具调用、Retrieval、Chunk、索引和前端行为不变。

## 非目标

- 不实现 Provider 原生 `response_format/json_schema`。
- 不修改所有 Provider 的 `complete()` 接口。
- 不增加宽松 JSON 片段提取。
- 不通过模糊匹配、自动改写 quote 或忽略未知 Evidence ID 让回答通过。
- 不改变无证据拒答策略。
- 不解决 Provider 网络、认证、配额或限流问题。

## 方案选择

### 采用：RAG 结构失败后严格重试一次

首次结构解析失败时，重新调用同一 Provider 和模型。第二次请求包含原问题、相同
Evidence、明确的 JSON 示例和严格字段说明。重试结果从头执行 JSON、Pydantic 和
引用校验。

该方案直接覆盖当前报错，修改范围集中，且不降低引用可信度。

### 不采用：本地宽松提取 JSON

从自由文本中寻找疑似 JSON 的方式可能把不完整回答误判为合法结果，无法保证引用
与结论一致。

### 暂缓：Provider 通用原生 Schema

通用原生 Schema 的长期稳定性更高，但当前改动范围明显超过“让简历与 JD 能正常
匹配”的最小目标。已有设计和计划继续保留，待确有多模型通用需求时实施。

## 数据流

```text
Question + Retrieved Evidence
        |
        v
第一次调用模型
        |
        v
JSON envelope + Pydantic 校验
        |
        +-- 成功 --> Evidence ID + 连续 quote 校验
        |                         |
        |                         v
        |                 canonical Citation
        |
        +-- 结构失败 --> 第二次调用模型
                              |
                              v
                    JSON envelope + Pydantic 校验
                              |
                  +-----------+-----------+
                  |                       |
                成功                    失败
                  |                       |
                  v                       v
        Evidence/Citation 校验   generation_invalid_output
```

## 首次生成

首次生成继续使用现有 Prompt 和 Evidence Context。现有行为保持不变：

- `tools=[]`；
- 只允许 `answered`、`refused`、`conflict`；
- claim 使用 `evidence_refs`；
- quote 必须是对应 Evidence 的连续原文；
- 返回内容只接受裸 JSON 或完整包裹结果的单个 JSON fence。

首次合法时立即进入引用校验，不产生额外模型调用。

## 第二次生成

第二次请求是一次完整但受限的重新生成，不是从自由文本中自动拼接 JSON。请求包含：

- 原问题；
- 与首次请求完全相同的 Evidence；
- 首次失败的脱敏类型；
- 明确的 JSON 输出示例；
- 禁止 JSON 之外说明的指令。

JSON 示例固定为：

```json
{
  "status": "answered",
  "answer": "基于证据的回答",
  "claims": [
    {
      "text": "一项可由证据支持的结论",
      "evidence_refs": [
        {
          "evidence_id": "E1",
          "quote": "E1 中的逐字连续原文"
        },
        {
          "evidence_id": "E2",
          "quote": "E2 中的逐字连续原文"
        }
      ]
    }
  ]
}
```

Prompt 需要明确：

- 不得同时输出旧格式 `evidence_ids/quote`；
- 每个 `evidence_refs` 项只对应一个 Evidence；
- 使用多份 Evidence 时，每份 Evidence 必须提供自己的 quote；
- 不得输出文件名、Chunk ID、版本或自造来源；
- 无法得到证据支持时返回 `refused`，不得编造匹配结论。

第二次调用仍使用 `tools=[]`，并使用同一个 Provider 和模型。

## 重试条件

以下结构错误允许重试一次：

- 不是有效 JSON；
- JSON 前后存在额外自由文本；
- 缺少 `status`、`answer` 或 `claims`；
- `status` 不是 `answered/refused/conflict`；
- claim 缺少 `evidence_refs`；
- `evidence_refs` 字段类型或内容不合法；
- 新旧引用格式混用；
- Evidence ID 重复；
- quote 为空。

以下错误不触发重试：

- Retrieval 无结果或 Evidence Gate 拒绝；
- Evidence ID 不属于本次 Retrieval；
- quote 不是对应 Evidence 的连续原文；
- Provider 超时、连接、认证、权限、限流、配额或内容安全错误；
- Citation canonical metadata 失效。

引用错误不属于格式错误。对引用失败重新生成可能掩盖模型不遵守证据的问题，因此
保持现有明确失败。

## 调用预算

每次 RAG 回答最多执行两次模型调用：

1. 首次生成；
2. 仅在首次结构失败时执行一次重新生成。

第二次结构仍失败时返回：

- code：`generation_invalid_output`
- HTTP status：`502`

不得进行第三次调用，不得把首次或第二次的未验证文本展示给用户。

## 修改范围

### `src/starter_agent/knowledge/generation.py`

- 保留现有严格 JSON envelope 解析；
- 将消息构造提取为可区分首次和重试的内部方法；
- 捕获首次 JSON/Pydantic/状态结构错误；
- 构造强化 Prompt 并调用同一模型一次；
- 第二次失败后返回现有稳定错误；
- 不捕获或转换 Citation 与 Provider 错误。

### `tests/unit/test_rag_generation.py`

- 记录 Provider 调用次数和每次消息；
- 验证首次成功只调用一次；
- 验证首次结构失败、第二次合法时成功；
- 验证第二次仍失败时停止；
- 验证 Provider 错误不触发重试；
- 验证 Citation 错误不触发重试；
- 验证第二次 Prompt 包含 JSON 示例和相同 Evidence。

### `tests/integration/test_rag_natural_query.py`

- 使用无敏感信息的简历和 JD；
- 验证自然语言问题能检索两类资料；
- 验证回答分别引用简历与 JD；
- 验证每个 quote 都能在对应 Evidence 中逐字定位。

不修改其他生产模块。只有测试确实需要复用 fixture 时，才调整既有测试辅助代码，
不得顺便重构无关功能。

## 错误处理与隐私

- 对外继续使用现有 `generation_invalid_output` 文案。
- 可在内部记录首次失败类别、模型名、调用次数和输出字符数。
- 不记录问题全文、简历、JD、Evidence、quote 或模型原始响应。
- 重试只发送到用户本次已经选择的同一 Provider，不跨 Provider 转发资料。

## 测试策略

### 单元测试

1. 首次返回合法新格式：调用一次并成功。
2. 首次返回普通文本、第二次返回合法 JSON：调用两次并成功。
3. 首次缺字段、第二次合法：调用两次并成功。
4. 首次新旧引用混用、第二次合法：调用两次并成功。
5. 两次均为非法结构：调用两次后返回 `generation_invalid_output`。
6. Provider 首次抛出超时：原错误透传，只调用一次。
7. 首次结构合法但 Evidence ID 未知：返回引用错误，只调用一次。
8. 首次结构合法但 quote 不连续：返回引用错误，只调用一次。

### 集成测试

使用 `tests/fixtures/knowledge/` 中无敏感信息的简历和 JD，验证：

- “我的简历匹配哪个岗位”能命中 `resume` 和 `job_description`；
- Generation 返回可解析的新格式；
- canonical citations 同时包含简历与 JD；
- 每个 quote 都是对应 Evidence 的连续原文。

### 回归测试

运行 focused RAG 测试后运行完整 pytest。测试临时目录放到系统临时目录，避免继续
在仓库中创建 `.pytest-*` 文件。

## 验收标准

功能通过必须同时满足：

1. 首次合法结果不会发生多余模型调用。
2. 首次结构无效时最多重试一次。
3. 第二次合法后仍执行完整引用校验。
4. 第二次失败后不展示模型文本。
5. 简历与 JD 的匹配回答同时包含两类可定位引用。
6. Evidence ID 和 quote 均通过严格校验。
7. Provider 与 Citation 错误不被结构重试掩盖。
8. focused tests 和完整 pytest 通过。

以下情况必须退回修复：

- 通过宽松提取或模糊 quote 让结果通过；
- 发生第三次模型调用；
- 只引用简历或只引用 JD 却给出完整匹配结论；
- 日志记录私人正文或模型原始响应；
- 只检查回答是否流畅，没有检查 Retrieval 和 Citation。
