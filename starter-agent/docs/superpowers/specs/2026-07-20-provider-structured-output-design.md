# Provider 通用结构化输出与一次修复重试设计

## 背景与问题

Starter Agent 的 RAG Generation 当前通过 Prompt 要求模型返回 JSON，再由
Pydantic、Evidence ID 校验和逐字引用校验决定结果能否展示。这一方式没有在
Provider 请求层约束输出格式。即使温度较低，模型仍可能返回额外说明、非法
JSON、缺失字段、新旧引用格式混用或非法状态，最终触发
`generation_invalid_output`。

本设计为所有 OpenAI-compatible Provider 增加可选的通用结构化输出能力，并在
首次结构校验失败时允许同一模型进行一次格式修复。引用规则不会因此放宽：
修复后的结果仍必须通过完整的 Schema、Evidence ID 和逐字 quote 校验。

## 设计目标

1. 为所有 OpenAI-compatible 模型提供统一、可选的结构化输出接口。
2. 模型服务支持原生 JSON Schema 时优先使用原生约束。
3. 模型服务不支持原生约束时，可以在当前请求内安全回退到严格 Prompt。
4. 首次结构化结果无效时，最多额外调用同一模型一次修复格式。
5. 格式修复不得补充事实、改写证据或绕过引用验证。
6. 普通 Chat、工具调用、Mock Provider 和现有非结构化调用保持兼容。
7. 错误诊断能够区分失败阶段，同时不记录私人文档或模型原文。

## 非目标

- 不放宽 quote 必须是对应 Evidence 连续原文的规则。
- 不使用模糊匹配替代引用校验。
- 不从任意自由文本中搜索、拼接或猜测 JSON。
- 不增加第二次以上的格式修复调用。
- 不建立持久化的 Provider 能力数据库。
- 不改变 Retrieval、Chunk、metadata filter 或索引实现。
- 不把结构化输出成功等同于 RAG 回答正确。

## 总体架构

结构化输出作为 Provider 的可选能力，由调用方通过统一描述对象声明。未声明
结构化输出的调用继续沿用现有行为。

```text
Question + Evidence
        |
        v
首次生成（携带 StructuredOutputSpec）
        |
        v
JSON envelope 解析 + Pydantic 校验
        |
        +-- 合法 --> Evidence ID 与逐字 quote 校验
        |                         |
        |                         v
        |                 canonical Citation
        |
        +-- 非法 --> 一次格式修复调用
                              |
                              v
                    再次解析与 Pydantic 校验
                              |
                 +------------+------------+
                 |                         |
               合法                      非法
                 |                         |
                 v                         v
       Evidence/Citation 校验    generation_invalid_output
```

Provider 的原生 Schema 参数若被目标服务明确拒绝，则当前请求回退到严格 Prompt
模式并重新执行首次生成。该兼容回退不计入“格式修复一次”的预算，因为它没有
产生可供修复的业务结果。

## 通用接口设计

新增不可变的结构化输出描述：

```python
class StructuredOutputSpec(BaseModel):
    name: str
    schema: dict[str, object]
    strict: bool = True
```

`Provider.complete()` 增加可选参数：

```python
async def complete(
    messages: list[Message],
    model: str,
    tools: list[dict[str, Any]],
    on_delta: Callable[[str], Awaitable[None]] | None = None,
    tool_choice: str | None = None,
    output_spec: StructuredOutputSpec | None = None,
) -> ModelResponse
```

所有现有调用点默认使用 `output_spec=None`。Mock Provider 和测试 Stub 接受新
参数但可以忽略，使接口升级不改变既有行为。

`StructuredOutputSpec.schema` 只描述 JSON 字段、类型和约束，不包含用户问题、
简历、JD、Evidence 或其他私人正文。

## OpenAI-compatible Provider 行为

当 `output_spec` 存在时，`OpenAICompatibleProvider` 将其转换为兼容接口的
`response_format/json_schema` 请求。流式与非流式请求使用相同的结构化输出
声明。

如果目标服务明确返回“`response_format` 或 JSON Schema 不受支持”的无效请求，
Provider 返回可识别的内部能力错误。RAG 调用方随后在当前请求内改用严格 Prompt
模式。认证、权限、限流、配额、连接、超时、内容安全和服务不可用错误不得被误判
为能力不支持，也不得触发格式修复。

能力回退只在当前请求内生效，不写入全局状态，避免一次临时错误永久改变 Provider
行为。后续若需要能力缓存，应作为独立设计处理。

## RAG 结构化 Schema

`RagGenerator` 从 Pydantic 模型生成请求 Schema，避免手工 Schema 与运行时模型
漂移。生成结果包含：

```json
{
  "status": "answered",
  "answer": "回答文本",
  "claims": [
    {
      "text": "事实性结论",
      "evidence_refs": [
        {
          "evidence_id": "E1",
          "quote": "对应 Evidence 中的连续原文"
        }
      ]
    }
  ]
}
```

`status` 仅允许 `answered`、`refused`、`conflict`。每个 claim 必须至少包含一个
`evidence_refs` 项；每项只绑定一个 Evidence ID 和该 Evidence 内的一段连续
quote。

运行时可以继续读取旧格式以兼容历史测试或调用方，但生成 Prompt 与原生 Schema
只要求新格式。新格式和旧格式同时出现仍视为无效结构，不能静默选取其中一种。

## JSON 解析边界

`StructuredOutputParser` 是纯解析与校验组件，负责：

1. 去除首尾空白；
2. 接受裸 JSON；
3. 接受内容完全由单个 `json` 或无语言标记代码块包裹的 JSON；
4. 执行 JSON 解析和目标 Pydantic 模型校验；
5. 返回脱敏的错误分类。

以下内容不进行宽松提取：

- JSON 前后存在解释或前言；
- 存在多个代码块；
- 从自然语言中只能找到疑似 JSON 片段；
- 字段缺失、类型不符或违反目标 Schema；
- 新旧引用格式混用。

这样可以避免把自由回答误识别为经过约束的结构化结果。

## 一次格式修复

只有首次响应已经成功返回，但 JSON 解析或 Pydantic Schema 校验失败时，才进入
格式修复。修复调用满足以下约束：

- 使用同一 Provider 和同一模型；
- `tools=[]`，禁止调用工具；
- 继续携带相同的 `StructuredOutputSpec`；
- 输入包含目标 Schema、首次输出和脱敏的格式错误类型；
- 明确要求只修复 JSON 与字段结构，不添加事实、不重新回答问题、不改写引用；
- 不追加原始 Evidence，避免模型利用修复步骤重新生成业务内容；
- 只允许调用一次。

修复结果重新经过同一个 Parser、Pydantic Schema、Evidence ID 和逐字 quote
校验。修复失败后立即返回 `generation_invalid_output`，不得进行第三次调用，
也不得展示首次或修复后的未验证文本。

由于首次输出可能包含用户资料的模型派生文本，修复调用只发送回同一个用户选择的
Provider 和模型，不跨 Provider 转发。

## 引用校验

结构化输出能力只解决传输格式，不改变证据可信性。`CitationValidator` 继续执行：

1. `evidence_id` 必须存在于本次 Retrieval 生成的 Evidence 集合；
2. `quote` 必须非空；
3. `quote` 必须是对应 Evidence 文本的逐字连续子串；
4. canonical 文件名、版本、Chunk ID、章节和行号只由服务端 Evidence 元数据
   组装，模型不能自行指定。

格式合法但引用不合法时，不进入格式修复。原因是引用失败属于证据错误而不是 JSON
格式错误，重新格式化不能将错误证据变成可信证据。

## 错误分类与前端行为

增加不包含私人内容的内部 `rule_id`：

| 阶段 | rule_id | 行为 |
| --- | --- | --- |
| 首次解析 | `invalid_json` | 进入一次格式修复 |
| Schema 校验 | `schema_validation_failed` | 进入一次格式修复 |
| 引用格式 | `mixed_reference_format` | 进入一次格式修复 |
| 状态校验 | `invalid_status` | 进入一次格式修复 |
| 修复结果 | `repair_invalid_output` | 返回 `generation_invalid_output` |
| 引用 ID | `citation_unknown_evidence` | 返回 `citation_validation_failed` |
| 引用原文 | `citation_quote_not_contiguous` | 返回 `citation_validation_failed` |

前端继续展示稳定的用户消息和建议，同时可以展示适合用户理解的失败类别。不得向
前端返回模型原始输出、Provider 原始错误正文或私人 Evidence。

## 日志与隐私

结构化输出诊断日志只允许记录：

- Provider 名称与模型名；
- 是否请求原生 Schema；
- 是否发生能力回退；
- 是否发生格式修复；
- 调用序号；
- `rule_id`；
- 输出字符数。

日志不得记录：

- 用户问题全文；
- 简历、JD 或 Evidence；
- 首次及修复后的模型输出；
- quote 原文；
- API Key、授权码或 Provider 原始响应正文。

## 兼容性与迁移

1. `Provider.complete()` 的新参数具有默认值，不要求现有调用点立即修改。
2. 普通 Chat 和工具调用不传 `output_spec`，请求结构保持不变。
3. Mock Provider、测试 Stub 和第三方 Provider 实现需要接受该可选参数。
4. RAG Generation 是首个使用通用结构化输出的调用方。
5. 旧版单 Evidence claim 输入仍可由 Pydantic 兼容层读取，但新生成只使用
   `evidence_refs`。
6. 不修改 Retrieval、文档生命周期、权限过滤和删除验证。

## 测试策略

### Provider 单元测试

- 有 `output_spec` 时正确发送原生 JSON Schema。
- 流式与非流式请求都携带相同 Schema。
- `output_spec=None` 时不发送 `response_format`。
- 目标服务明确不支持 Schema 时产生可识别的能力错误。
- 认证、权限、限流、超时和连接错误不被归类为能力不支持。

### Parser 单元测试

- 接受裸 JSON。
- 接受单个完整 JSON fence。
- 拒绝前后说明、多 fence、非法 JSON 和 Schema 不匹配。
- 对缺失字段、非法状态、新旧格式混用和重复 Evidence ID 返回稳定诊断码。

### RAG Generation 单元测试

- 首次合法输出不触发修复。
- 首次结构错误只触发一次修复。
- 修复成功后继续完成引用校验和 canonical Citation 组装。
- 修复失败后停止，不发生第三次调用。
- Provider 业务错误不触发格式修复。
- Schema 能力回退不占用格式修复预算。
- 引用失败不触发格式修复。

### 集成与回归测试

- 使用无敏感信息的简历和 JD fixture 完成跨文档回答。
- 每个 claim 的每个引用都能定位对应 Chunk 原文。
- 未知 Evidence ID 与非连续 quote 返回不同 `rule_id`。
- 无证据问题仍明确拒答。
- 普通 Chat、工具调用、Mock Provider 和现有 RAG 测试保持通过。
- 使用 `glm-4.7` 和无敏感 fixture 执行一次真实链路验证，分别保存 Retrieval
  断言、结构校验结果、Generation 状态和引用校验结果；不以回答流畅度作为通过
  标准。

## 验收标准

功能仅在同时满足以下条件时通过：

1. OpenAI-compatible Provider 可以按统一接口请求结构化输出。
2. 不支持原生 Schema 的服务能够安全回退，且不会掩盖其他 Provider 错误。
3. 首次结构失败最多触发一次修复，任何路径都不会发生第三次调用。
4. 未验证的首次或修复输出不会展示给用户。
5. 回答通过 Pydantic Schema、Evidence ID 和逐字 quote 校验。
6. canonical Citation 完全由服务端元数据生成。
7. 日志和错误响应不包含私人正文或模型原始输出。
8. 普通 Chat、工具调用及现有 Provider 行为没有回归。
9. 无敏感 fixture 的 `glm-4.7` 真实链路通过结构与引用断言。

以下任一情况必须退回修复：

- 通过模糊匹配、自动改写 quote 或忽略未知 Evidence ID 让回答通过；
- 模型返回结构无效时仍展示自然语言答案；
- 格式修复超过一次；
- Provider 认证、限流或安全错误被误判为 Schema 不支持；
- 日志记录了用户文档、Evidence、quote 或模型原始输出；
- 只验证“回答看起来合理”，没有分别验证 Retrieval、Schema 和 Citation。
