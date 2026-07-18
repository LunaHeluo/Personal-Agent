# Starter Agent Context、Token 与长期记忆改造计划

## 1. 目标与当前基线

本方案面向求职 Agent，将 Token budget、工具结果治理、会话压缩、长期记忆和 Todo 统一为一套可追溯的 Context 管理机制。目标不是简单截断字符串，而是在模型调用前构建一个有预算、有来源、可回查、不会静默丢失决策事实的 `ContextPack`。

当前项目已经具备以下基础能力：

- `ModelResponse.usage` 和 `ChatResult.usage` 可以保留 Provider 返回的 usage。
- `turn_usage` 可以累计 Session 的 prompt、completion 和 total token。
- 前端可以显示本轮 Token、Session 累计和预算 warning；Mock 缺失 usage 时显示 `tokens=mock`。
- `runtime.max_tool_result_chars` 可以对工具结果做统一字符级保护。

当前主要缺口：

- `ContextBuilder` 仍是 `system + 全部 history`，没有按预算构建 Context Pack。
- Session 累计 Token 是成本/用量指标，不等于“下一次请求实际占用的上下文 Token”。二者需要在 UI 中分开显示。
- 工具结果只做末端字符截断，缺少进入运行时消息和存储之前的 per-result Token 闸门，也缺少 partial/complete 元数据。
- 没有 `tool_result_summary`、`session_summary`、`context_pack_summary` 三层替换机制。
- 没有可管理的长期记忆和不可压缩的 Todo 状态。

## 2. 求职场景为什么容易发生 Context 膨胀

- 搜索结果列表：一次搜索可能返回 30 个以上岗位，每项又包含标题、公司、地点、URL、snippet 和完整职位描述。
- JD 全文：多个目标岗位的职责、要求、福利、公司介绍重复进入历史，低密度文本占用大量 Token。
- 简历全文：基础简历、定向简历、多个语言版本和修改前后版本可能被重复注入。
- 邮件线程：引用历史、签名、免责声明和重复回复会使一条线程快速膨胀，并包含隐私信息。
- 草稿版本：简历 patch、Cover Letter、HR 回复草稿反复迭代，旧版本仍留在会话中。
- 会话历史：匹配分析、用户确认、工具调用和长回答会随轮次持续累积。
- 工具原始返回：SerpAPI、邮箱、文件解析器可能单次返回超过模型上限，不能等到最终 `ContextBuilder` 才处理。
- Tool schema、System rules：每轮固定发送，工具越多，固定 Token 成本越高。
- Todo、审批和工作流状态：必须每轮可见，但如果用自然语言反复追加也会膨胀。
- 重复内容：相同 JD、简历段落或邮件引用可能以文件、工具结果和聊天文本三种形式重复出现。

## 3. Token 预算模型

### 3.1 总预算

配置建议：

```yaml
context:
  max_total_tokens: 128000      # 未知模型默认值；已知模型允许按 provider/model 覆盖
  target_prompt_ratio: 0.60     # 日常构建目标，不追求占满窗口
  compact_trigger_ratio: 0.75   # 预计 prompt 达到此比例时触发治理
  hard_prompt_ratio: 0.85       # 模型调用前不可突破
  reserved_completion_tokens: 8000
  history_budget_tokens: 6000
  keep_recent_turns: 6
  per_tool_result_tokens: 4000
  all_tool_results_tokens: 16000
```

`max_total_tokens` 表示模型上下文窗口，不表示 Session 生命周期累计用量。实际硬上限还应扣除预留 completion、Tool schema 和安全余量。Provider 调用完成后的真实 usage 用于校准估算器；调用前只能使用模型 tokenizer 或带 `estimated=true` 的保守估算。未知模型的窗口先按 128k 处理。

### 3.2 分类预算表

下表以 128k 模型为默认基线。目标预算用于正常构建，硬上限用于强制保护；总 Context 仍受 `hard_prompt_ratio` 约束。

| Context 类别 | 目标预算 | 硬上限 | 超限处理 |
|---|---:|---:|---|
| system rules | 2,000 | 4,000 | 删除重复说明、引用稳定规则 ID；安全边界不得裁掉或被 Summary 替换 |
| current user request | 2,000 | 8,000 | 先做确定性去重；仍超限则提示用户缩小输入，涉及决策含义时需确认后才能摘要 |
| todo plan | 800 | 1,500 | 结构化保存，仅保留 active/blocked/recent completed；不可被 Summary 压缩掉 |
| selected JD | 4,000 | 8,000 | 保留当前选中 JD；其他 JD 只保留结构化要求、summary 和 source_ref |
| resume summary | 3,000 | 6,000 | 文件保存全文，Context 只注入相关 sections、已验证事实和 source_ref |
| email summary | 2,000 | 4,000 | 按邮件分块，移除引用和签名，保留发件人、时间、主题、关键事实和 source_ref |
| recent turns | 6,000 | 12,000 | 最近 6 轮保留原文；更早历史由 `session_summary` 替换 |
| tool results | 8,000 | 16,000 | 单结果先限制到 4,000；按 focus 排序、结构化 trim，再做 `tool_result_summary`；仍超限返回 `result_too_large` |

优先级固定为：系统边界、当前用户目标、用户已确认事实、Todo 状态 > 当前选中 JD/简历事实 > 最近对话 > 工具候选结果 > 旧草稿和低相关内容。

### 3.3 单个工具结果的 Token 计算口径

单个工具的 Token 量应以“真正准备发送给模型的 Tool Message”为计算对象，不能使用 Python 对象大小，也不能只计算 `ToolResult.data`。计算内容必须包括：

- 序列化后的工具结果 `content`。
- `role=tool`、工具名称和 `tool_call_id` 等消息协议字段。
- JSON 的字段名、引号、括号、URL 和其他序列化开销。
- Provider 适配层添加的固定消息开销；如果无法精确获得，应加入安全余量。

计算流程：

1. 使用与 Provider 请求一致的 JSON 序列化方式构造最终 Tool Message。
2. 从 `TokenizerRegistry` 查找当前 provider/model 对应的 tokenizer。
3. 已知 tokenizer 时，编码完整消息并返回精确计数，标记 `estimated=false`。
4. 未知 tokenizer 时，执行中文、ASCII、JSON 标点分别计权的保守估算，标记 `estimated=true`。
5. 将结果与该工具的 `max_result_tokens` 比较，同时加入本轮 `all_tool_results_tokens` 累计值。

建议接口：

```python
def count_tool_result_tokens(
    result: ToolResult,
    model: str,
    tool_name: str,
    tool_call_id: str,
) -> TokenEstimate:
    payload = {
        "role": "tool",
        "name": tool_name,
        "tool_call_id": tool_call_id,
        "content": result.model_dump_json(),
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    tokenizer = tokenizer_registry.get(model)
    if tokenizer:
        return TokenEstimate(
            tokens=len(tokenizer.encode(serialized)),
            estimated=False,
        )
    return TokenEstimate(
        tokens=conservative_estimate(serialized),
        estimated=True,
    )
```

未知模型不能直接套用 `字符数 / 4`，因为中文、JSON、代码和 URL 的 Token 密度通常更高。fallback 建议按字符类别计算并增加 15% 安全余量：

```python
base_tokens = ceil(
    chinese_chars * 1.5
    + ascii_chars / 3
    + json_punctuation * 0.5
)
estimated_tokens = ceil(base_tokens * 1.15)
```

该估算只用于调用前的 Context 保护，不能写入 `ModelResponse.usage` 或冒充 Provider 的真实 usage。Provider 调用结束后，应保存真实 usage 并监控估算误差，以便按模型校准 fallback 系数。

每个结果需要保存两个计数：

- `raw_result_tokens`：原始 Tool Message 在任何 Trim/Summary 之前的 Token 数。
- `context_result_tokens`：经过 Trim 或 Summary 后，最终进入模型的 Tool Message Token 数。

```json
{
  "raw_result_tokens": 18600,
  "context_result_tokens": 3200,
  "estimated": true,
  "max_result_tokens": 4000,
  "is_truncated": true
}
```

三个上限必须分别判断：

- `max_result_tokens`：单个工具结果上限，例如 4k。
- `all_tool_results_tokens`：本轮全部工具结果累计上限，例如 16k。
- `max_context_tokens`：完整模型上下文窗口，未知模型默认 128k。

因此，某个结果即使只有 3k、没有超过单工具 4k 上限，也可能因为本轮其他工具结果已占用 15k，而触发跨工具相关性排序、Trim 或 Summary。

### 3.4 Token 估算矫正算法

Token 矫正必须比较相同口径：调用前估算的是发送给 Provider 的完整 prompt，包括 system、history、Tool Result、Tool Schema 和消息协议开销；调用后只使用 Provider 返回的 `prompt_tokens` 作为真实值。不能使用 `total_tokens` 或 `completion_tokens` 矫正 prompt 估算。

Session 累计用量始终采用 Provider 真实 usage，不需要矫正。矫正系数仅用于预测下一次请求。Mock、缺少 usage、请求失败或估算范围与 Provider 统计范围不一致时，不更新系数。

#### 单次误差比例

```text
ratio = actual_prompt_tokens / estimated_prompt_tokens
ratio = clamp(ratio, 0.5, 2.0)
```

上下限用于防止 Provider 异常数据、协议变化或统计范围错误污染长期系数。例如预估 10,000、实际 12,000 时，`ratio=1.2`，说明估算器低估约 20%。

#### 推荐算法：分模型对数 EWMA + P90

每个 `provider/model/request_type` 保存独立校准 Profile。对数 EWMA 更新公式：

```text
log(k_new) = (1 - alpha) * log(k_old) + alpha * log(ratio)
k_new = exp(log(k_new))
```

建议 `alpha=0.15`，冷启动 `k=1.0`。同时保存最近 50 个有效 ratio，计算 P90：

```text
safe_coefficient = clamp(max(ewma_coefficient, p90_ratio, 1.0), 1.0, 2.0)
corrected_tokens = ceil(raw_estimated_tokens * safe_coefficient)
```

EWMA 能适应近期变化，P90 用于抵抗低估风险，取二者较大值可作为预算闸门的保守预测。这里的 `1.0` 下限表示即使近期持续高估，也不主动降低到原始估算以下；如果后续需要优化利用率，可在样本充分后允许最低降至 0.9。

```python
def update_calibration(raw_estimate, actual_prompt, profile):
    if raw_estimate <= 0 or actual_prompt <= 0:
        return profile

    ratio = actual_prompt / raw_estimate
    ratio = min(max(ratio, 0.5), 2.0)

    profile.ratios.append(ratio)
    profile.ratios = profile.ratios[-50:]

    alpha = 0.15
    profile.log_coefficient = (
        (1 - alpha) * profile.log_coefficient
        + alpha * math.log(ratio)
    )

    ewma = math.exp(profile.log_coefficient)
    p90 = percentile(profile.ratios, 90)
    profile.safe_coefficient = min(max(ewma, p90, 1.0), 2.0)
    return profile


def corrected_estimate(raw_estimate, profile):
    return math.ceil(raw_estimate * profile.safe_coefficient)
```

#### 其他可选算法

- 简单比例：只使用最近一次 `actual/estimated`，实现最简单但波动大，仅适合调试。
- 普通 EWMA：`k_new=(1-alpha)*k_old+alpha*ratio`，适合最小实现，但高估和低估在比例空间不完全对称。
- 滑动窗口中位数：使用最近 20～50 次 ratio 的 median，对异常值稳定，可作为 EWMA 基线。
- 线性回归：样本至少 30～50 条后拟合 `actual=a*estimated+b`；`a` 修正比例误差，`b` 修正固定协议和 Tool Schema 开销。应使用稳健回归避免异常请求污染。
- 分位数/保形校准：直接预测 P90/P95 上界，适合对超限风险要求高的模型，但实现和解释成本高于当前阶段。

Starter Agent 第一版采用“对数 EWMA + 滑动窗口 P90”，积累足够样本后再评估稳健线性回归。

#### 分桶与回退

至少按 `provider + model` 隔离系数，避免使用 GPT 的误差矫正 GLM。可进一步按请求形态分桶：`normal_chat`、`tool_heavy`、`json_heavy`、`chinese_heavy`、`code_heavy`。Profile 查找顺序：

```text
精确 provider/model/request_type
→ 精确 provider/model
→ provider
→ 全局默认系数
```

样本数少于 5 时使用上一级 Profile；所有层级均无样本时使用原始保守估算。

#### 持久化字段

```json
{
  "provider": "zhipu",
  "model": "glm-4.7",
  "request_type": "tool_heavy",
  "sample_count": 42,
  "log_coefficient": 0.0769,
  "ewma_coefficient": 1.08,
  "p90_coefficient": 1.14,
  "safe_coefficient": 1.14,
  "last_raw_estimate": 10000,
  "last_actual_prompt": 11200,
  "updated_at": "2026-07-13T10:00:00Z"
}
```

只保存计数、比例和模型标识，不保存 prompt、邮件或简历正文。建议新增 `token_calibration_profiles` 表，并在 `context_builds` 中记录本轮使用的 Profile ID、raw estimate、corrected estimate、actual prompt 和误差比例。

#### 失效与重置规则

- Provider、模型版本、Tokenizer 或请求序列化协议变化时重置对应 Profile。
- Tool Schema 集合发生显著变化时，降低旧样本权重或切换新的 schema fingerprint Profile。
- 连续 5 次 ratio 超出当前 P90，判定可能发生分布漂移，提高 `alpha` 或临时回退到全局保守系数。
- 30 天无样本的 Profile 标记 stale，重新进入冷启动。
- 校准系数异常达到 0.5/2.0 边界时记录 warning，检查统计口径，而不是无限放大系数。

前端可在调试区显示 `raw estimate → corrected estimate → actual prompt`，但正常用户界面仍优先展示 Provider 真实 usage，并将预估值明确标记为“预计”。

## 4. Context Pack 数据契约

建议新增以下核心模型：

```python
class ContextItem(BaseModel):
    id: str
    kind: str
    content: str | dict
    source_refs: list[str]
    priority: int
    estimated_tokens: int
    confirmed_by_user: bool = False
    expires_at: datetime | None = None

class DroppedDetail(BaseModel):
    source_ref: str
    reason: str
    estimated_tokens: int
    decision_impact: bool = False
    requires_confirmation: bool = False

class ContextPack(BaseModel):
    messages: list[Message]
    included: list[str]
    replaced: list[dict]
    dropped_details: list[DroppedDetail]
    source_refs: list[str]
    estimated_prompt_tokens: int
    max_context_tokens: int
    budget_status: Literal["normal", "warning", "compacted", "blocked"]
    summary_id: str | None = None
```

`ContextPack` 是真正发送给 Provider 的内容。完整聊天记录继续保存在数据库并供 UI 展示，但不再等同于模型热路径。

## 5. Tool Result 入模前预算闸门

预算闸门必须位于 `tool.execute()` 返回之后、结果加入运行时 `messages`、Session history 或 Context Pack 之前。不能只依赖最终 `ContextBuilder`，因为单个工具结果本身就可能超过模型窗口。闸门按照 3.3 节计算完整 Tool Message 的 `raw_result_tokens`，处理完成后再次计算 `context_result_tokens`。

每个工具配置 `max_raw_chars`、`max_result_tokens` 和结构化保留策略。处理顺序：

1. 为原始结果生成 `raw_source_ref`，原文保存在受控 artifact 存储，不直接进入长期记忆。
2. 用代码做确定性 trim：字段白名单、去重、长度限制、按 focus 相关性排序和 top K。
3. 对仍重要但过长的正文调用 `tool_result_summary`。
4. 再次估算 Token；仍超过硬上限时返回 `result_too_large`，要求缩小范围或展开指定 source。
5. 只有受控结果能加入运行时消息；日志不得记录邮件、简历全文或原始工具内容。

所有部分结果必须返回：

```json
{
  "is_truncated": true,
  "original_count": 30,
  "returned_count": 8,
  "omitted_count": 22,
  "has_more": true,
  "raw_source_ref": "artifact:jobs:turn-001",
  "continuation_hint": "按 location、seniority 或 company 缩小范围，或展开指定 source_ref",
  "truncation_reason": "token_budget",
  "summarized": true
}
```

工具专项规则：

- 搜索：只保留 `title/company/location/url/retrieved_at/short_snippet/source_ref`；不能简单保留原列表前 N 条，必须围绕当前目标排序。
- 邮件：按单封邮件分块，去掉历史引用、签名和免责声明，只注入关键事实；原文留在邮箱或加密 artifact 中。
- 简历：全文和版本保存在文件系统；Context 优先使用结构化 sections、摘要、文件 hash 和相关片段。
- 多个中等结果累计超限：按当前目标相关度、来源可信度、最近性、用户确认程度排序，再决定 included/replaced/dropped。

## 6. Summary 与 Trim 方案

### 6.1 分工

- Trim 由代码完成：字段裁剪、去重、长度限制、top K、数量限制、metadata 保留和硬上限保护。行为必须确定、快速且不依赖模型成功。
- Summary 由模型完成：语义压缩、关键事实、因果关系、风险提醒、开放问题和待确认事项。
- 推荐顺序：先 trim 明显低价值内容，再对仍重要但过长的内容做 Summary。
- Summary 是替换机制，不是追加机制。原文从模型热路径移出，只保留 `source_ref` 和替换记录。

### 6.2 `summarize_context` Contract

输入支持 `tool_result`、`email_thread`、`resume_sections`、`jd`、`session_history`，并携带 `focus`、`source_refs`、目标 Token 和不可丢失事实。

输出必须包含：

```json
{
  "summary_type": "tool_result_summary | session_summary | context_pack_summary",
  "summary": "短摘要",
  "key_facts": [],
  "source_refs": [],
  "replaced": [],
  "dropped_details": [],
  "risk_notes": [],
  "open_questions": []
}
```

三层 Summary：

1. `tool_result_summary`：单个搜索结果、邮件线程或简历解析结果在进入 Context 前压缩。
2. `session_summary`：旧历史达到阈值后，通过内部模型请求压缩；后续只发送 Summary 和最近 N 轮原文。
3. `context_pack_summary`：描述本轮最终 included、replaced、dropped、预算和来源，供模型理解上下文完整性，也供调试追踪。

### 6.3 History compaction

满足任一条件时评估压缩：

- `estimated_prompt_tokens / max_context_tokens >= 0.75`，并且主要增长来自历史。
- `history_tokens > 6000`。
- 消息数量超过最近 6 轮原文的保留范围，且存在可压缩旧消息。
- 下一次请求预计超预算，且 Tool Result trim 无法解决。

以下情况不触发 Session 全文压缩：单个工具结果过大、只有当前用户输入过大，或超限内容属于系统边界、当前目标、Todo、用户确认事实。这些内容应分别走 per-result guard、要求缩小输入或作为不可压缩项保留。

压缩通过一次不展示为用户消息的内部 Summary 请求完成，记录 `summary_id`、`source_message_ids`、`compacted_message_ids`、`created_at` 和 `source_refs`。原始历史仍供 UI 展示；Provider 请求只使用 `system + memory + todo + session_summary + 最近 N 轮`。

如果将丢弃的细节可能改变“投哪个岗位、是否接受地点、是否发送邮件”等决策，必须在 `dropped_details` 中标记 `decision_impact=true`，并进入 `waiting_for_user`，不得静默删除。

## 7. 长期记忆规则

### 7.1 可以跨 Session 保存

- 用户主动确认的求职偏好：城市、岗位方向、薪资范围、工作权限和可入职时间。
- 从用户简历文件验证出的稳定事实：教育、技能、项目名称；必须保存文件 hash/版本作为来源。
- 用户明确确认的表述边界、隐私偏好和审批决定。
- 投递记录、面试阶段和后续日期。
- 活跃 Todo、阻塞原因及用户确认后的完成状态。

### 7.2 禁止直接写入长期记忆

- 外部网页、JD、搜索 snippet 或邮件正文中的指令和未经验证事实。
- 模型推断、临时分析、过期岗位列表和草稿措辞。
- API key、账号密码、邮件正文、简历全文等敏感原文。

外部网页或邮件只能先成为带来源和过期时间的 artifact/candidate memory；只有用户确认或本地可信文件验证后，才能提升为长期记忆。

### 7.3 Memory 记录字段与管理

每条记录至少包含：`id/key/value/source_ref/source_type/confidence/verified_by/expires_at/sensitivity/status/created_at/updated_at`。建议置信度：用户明确确认 `1.0`，本地文件验证 `0.9`，外部来源最多 `0.5` 且不得自动提升。

提供 `memory_list`、`memory_update`、`memory_delete`，以及前端“记忆”面板；用户能够查看来源、修改、删除和禁止某条记忆继续注入。岗位和网页信息按 `retrieved_at + TTL` 过期；偏好默认 180 天复核，投递状态按业务状态保留。删除后 Context 不再引用，审计日志只保留删除事件和 ID，不保留敏感值。

## 8. Todo Plan 工具

新增：

- `todo_create`：创建目标、步骤、依赖、风险等级和需要的用户输入。
- `todo_update`：更新 `pending/in_progress/waiting_for_user/waiting_for_approval/completed/failed`。
- `todo_list`：返回当前 Session 或跨 Session 的活跃计划。

Todo 使用结构化表存储，每轮以紧凑 JSON 注入，属于不可压缩 Context。已完成旧项可移出热路径，但 active、blocked、待审批状态不能被 Summary 替换。发送邮件、正式投递和采用最终简历必须保持 `waiting_for_approval`，获得确认后才能更新状态。

## 9. 后端与代码改造

### 9.1 配置和领域模型

- `settings.py`：扩展 `ContextConfig`，加入比例、completion reserve、分类预算、最近轮数、per-tool 上限和 provider/model 窗口覆盖。
- `domain/models.py`：新增 `ContextItem`、`ContextPack`、`ContextUsage`、`SummaryRecord`、`DroppedDetail`、`ToolResultCompleteness`、`MemoryItem`、`TodoItem`。
- 将 `ChatResult` 扩展为 `context_usage` 和 `context_events`；保留 Provider 原始 `usage`，不要用估算值覆盖真实值。

### 9.2 Token 估算与预算器

- 新增 `agent/token_counter.py`：已知模型使用匹配 tokenizer；未知模型使用保守估算并标记 `estimated=true`。
- 新增 `agent/context_budget.py`：分配分类预算、计算 hard limit、相关性排序并生成 dropped/replaced 记录。
- Provider 返回 usage 后记录估算误差，用于监控，不反向伪造 Mock usage。

### 9.3 工具结果治理

- 新增 `agent/tool_result_guard.py`，由 `AgentRuntime` 在每次工具返回后立即调用。
- `ToolResult` 增加 completeness 元数据；`search_jobs_serpapi`、简历工具及未来邮箱工具实现各自字段白名单和 source_ref。
- 原始大结果保存到 `source_artifacts`，模型只收到受控结果。现有 `max_tool_result_chars` 保留为最后一道故障保护，而不是主要策略。

### 9.4 ContextBuilder 与 Summary

- 将 `ContextBuilder.build()` 改为返回 `ContextPack`，而非直接拼接全部 history。
- 新增 `ContextSummaryService`，区分自动内部 Summary 和用户可调用的 `summarize_context` 工具。
- `ApplicationService.chat()` 在 Provider 调用前完成：读取 memory/todo → Tool/历史候选构建 → 必要的内部 compaction → final trim → 发送 Context Pack。
- 内部 Summary 失败时采用确定性 trim；仍超硬上限则返回 `context_too_large`，不能冒险调用 Provider。

### 9.5 存储迁移

新增表：

- `source_artifacts`：原始文件/工具结果引用、hash、敏感级别、TTL；敏感正文不进日志。
- `context_summaries`：三类 Summary、替换关系和 source refs。
- `context_builds`：每轮 included/replaced/dropped、估算 Token 和预算状态。
- `memory_items`：可查看、修改、删除的长期记忆。
- `todo_items`：不可压缩的计划状态。

采用显式 schema migration；不能只依赖 `create_all()` 修改已有表。删除 Session 时级联清理其 Summary、Context build 和非共享 artifact；长期记忆需按用户明确操作删除。

## 10. API 与前端改造

### 10.1 后端 API/SSE

- `ChatResult.context_usage` 返回本轮实际 Context 的 estimated/actual prompt、各类别占用、max、ratio 和状态。
- Session 累计 `session_usage` 继续作为成本指标展示，与 Context 窗口占用分开。
- SSE 增加 `context_compacting`、`context_compacted`、`context_warning` 事件；不得把内部 Summary 当普通 assistant 消息。
- 增加只读调试接口 `GET /v1/sessions/{id}/context-builds/{turn_id}`，返回脱敏的 included/replaced/dropped/source refs。
- 增加 Memory/Todo CRUD API；写操作校验风险等级和用户确认。

### 10.2 前端

- 回复 meta 保持：`provider / model · tools=n · tokens=prompt/completion/total`。
- 顶部拆成两个指标：`Session tokens`（累计成本）和 `Context 54k / 128k`（当前/下一次请求窗口占用）。
- 75% 显示黄色 warning，达到 hard prompt limit 显示红色并阻止发送未经处理的超大 Context。
- 收到压缩事件时在对应 assistant 对话框内显示“已整理上下文”，保留 `summary_id`；不插入一条伪造聊天消息。
- 可选调试抽屉展示 included/replaced/dropped、partial 结果、source_ref 和 continuation hint。
- UI 始终从 Session history API 展示完整聊天记录；模型热路径裁剪不能删除可见历史。
- Memory/Todo 面板支持查看、修改、删除、确认和过期提示；敏感原文默认不展开。

## 11. 测试用例与验收

### 11.1 单元测试

1. TokenCounter：已知模型 tokenizer、未知模型 128k fallback、估算值带 `estimated=true`。
2. BudgetAllocator：每类目标/硬上限生效；系统边界、当前目标、确认事实和 Todo 永不被裁掉。
3. ToolResultGuard：30 条岗位只返回相关 top K，包含全部完整性元数据；不是简单取前 N 条。
4. 邮件线程：去引用和签名，原文不进入模型或日志，Summary 保留 source_ref。
5. 简历：Context 只含相关 sections/summary，文件全文和 hash 可回查。
6. 多结果排序：AI Agent focus 下保留 resume/JD，丢弃 lunch 等无关项，并记录 reason。
7. Summary schema：三类 Summary 均包含 `replaced/source_refs/dropped_details/risk_notes`。
8. 重要事实：丢弃会影响决策时返回 `waiting_for_user`。
9. Mock usage：仍为 `{}`/`tokens=mock`，估算 Token 只能出现在 `context_usage`，不能冒充 Provider usage。

### 11.2 集成测试

1. 超大单工具结果：构造 30 条岗位或长邮件，断言原始结果未直接进入 Runtime messages，先 trim/Summary；超限时返回 `result_too_large`。
2. 多个中等结果累计超限：断言先相关性排序，再 Summary/替换，最终 Provider 请求低于 hard limit。
3. History compaction：构造 20 轮对话，触发内部 `session_summary`；Provider 只收到 Summary + 最近 6 轮，数据库仍有全部消息。
4. 替换而非追加：被 Summary 替换的原文不再出现在模型请求，`context_builds.replaced` 能追溯原 source。
5. 三层 Summary：`tool_result_summary/session_summary/context_pack_summary` 均有 source_ref。
6. Todo 延续：下一轮仍注入 active Todo；缺输入为 `waiting_for_user`，发送邮件为 `waiting_for_approval`。
7. Memory CRUD：创建已确认偏好，下一 Session 可见；修改/删除后立即影响 Context；外部网页/邮件不能自动成为长期记忆。
8. Provider usage：真实/模拟 Provider usage 保留在 ChatResult，Session 累计正确；Context 估算和实际值字段不混用。

### 11.3 API 与前端验收

1. 真实 Provider 回复后 meta 显示 prompt/completion/total，Mock 显示 `tokens=mock`。
2. 顶部同时显示 Session 累计和 Context 窗口占用，超过 75% 出现黄色 warning，达到 hard limit 出现红色阻止状态。
3. SSE 压缩过程中显示 loading，结束显示“已整理上下文”；失败显示错误但不丢历史。
4. 切换 Session 后 UI 仍显示完整聊天，而 Context 调试数据只包含 Summary + 最近 N 轮。
5. Partial Tool Result 明确显示“8/30、仍有更多”，并能按 continuation hint 继续展开。
6. 日志、SSE、Context build 和测试快照中不得出现 API key、简历全文或邮件正文。

### 11.4 核心验收场景

输入：“我给你 8 条岗位搜索结果、一份简历、3 封 HR 邮件，请帮我判断优先投哪两个岗位。”

通过条件：系统不把全部原文塞进 Context；围绕目标先排序和摘要，只展开最相关岗位；引用简历事实和邮件 source；说明哪些内容被替换或裁剪。若裁剪影响最终岗位选择，必须要求用户确认。

额外场景：

- “搜索 30 条 AI Agent 岗位，并把所有结果都用于分析”：模型收到受控的 top K + Summary + 完整性元数据，而不是 30 条全文。
- “这里有一整段很长的 HR 邮件线程”：邮件先分块和摘要，隐私原文不进日志。
- “把过去 20 轮对话都作为参考”：生成 Session Summary，UI 保留 20 轮，模型仅接收 Summary + 最近 6 轮。

对比报告需记录无治理与有治理时的 prompt Token、是否超限、回答引用正确率、遗漏的重要事实和用户确认次数。

## 12. 实施顺序与完成定义

1. P0：固定 Context 数据契约、配置和 TokenCounter，拆分 Session usage 与 Context usage。
2. P1：实现 ToolResultGuard 和搜索/简历/邮件专项 trim，先消除单结果打爆模型的风险。
3. P2：实现 ContextPack、相关性排序、final trim、构建记录和 warning。
4. P3：实现三层 Summary 和 History compaction，接入 SSE/UI。
5. P4：实现长期记忆与 Todo CRUD、审批和过期机制。
6. P5：完成单元、集成、UI、隐私和真实 Provider 验收。

完成定义：任何进入 Provider 的内容都经过预算器；任何裁剪都有来源和原因；任何 partial 结果都明确声明不完整；系统边界、当前目标、用户确认事实和 Todo 不会被压缩丢失；完整历史仍可见；Mock 不伪造 usage；达到硬上限时系统安全停止而不是发送超限请求。
