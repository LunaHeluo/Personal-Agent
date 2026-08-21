# Job Research 实施前仓库审计

## 审计范围与结论

本审计以当前工作区代码为准，覆盖 `job-research` 链路、Agent Runtime、ContextBuilder、Tool Registry、MCP Client Manager、Skill Registry、Pre-Tool-Call Gate、Trace、JSONL Log、Token Usage、错误映射和前端路由。

当前仓库已经具备 `search_jobs_serpapi`、`retrieve_resume_evidence`、`job-research` Skill、`JobResearchOrchestrator`、Unified Tool Registry、Playwright MCP 能力接入、Pre-Tool-Call Gate、确认卡、capability audit event、context snapshot、turn usage 与 `tool_artifacts`。这些应作为信任层第一优先复用对象。

当前仍没有专用 Eval Runner、没有固定求职调研 Fixture 目录、没有 Eval Run/Case 存储、没有完整 Trust Center、没有真实模型 Smoke 独立报告；现有 Trace 也缺少独立 model_request_id 和缺少独立 policy_decision_id。以上缺口需要在 `job-research-trust` 后续任务中新增或扩展，不应臆造为已存在能力。

## 配置加载与应用生命周期

- `backend/src/starter_agent/settings.py`：`load_settings()` 读取 `STARTER_AGENT_CONFIG` 指向的 YAML，默认使用 `config/config.yaml`；相对路径按项目根目录解析。`AgentSettings._environment_value()` 先读进程环境变量，再读取项目根目录 `.env`。Provider、SerpAPI、邮件等秘密只通过环境变量名解析。
- `backend/src/starter_agent/bootstrap.py`：`create_application()` 组装 `SQLiteSessionStore`、`ProviderRegistry`、旧 `ToolRegistry`、`AgentRuntime`、`ContextBuilder`、MCP/capability/skill 相关服务和日志配置。
- `backend/src/starter_agent/application.py`：`ApplicationService` 是聊天、知识库与 `job-research` 应用入口；它负责准备上下文、调用 Runtime、保存消息、保存 `turn_usage`、保存受限 `tool_artifacts`，并在后台执行记忆维护。

## Agent Runtime、Context 与 Tool 调用链

- `backend/src/starter_agent/agent/runtime.py`：`AgentRuntime` 驱动模型循环。模型请求前会从具备 `model_snapshot()` 的 registry 读取 callable tool snapshot，把 `provider_tools()` 交给 Provider，并写入 `model.context.snapshot`、`model.requested`、`model.completed` 等 audit event。当前模型调用用 `call_id` 形如 `model-call-{N}`，尚未形成独立稳定的 `model_request_id` 字段。
- 同一 Runtime 在模型请求 Tool 后写 `tool.requested`，调用 `PreToolCallGate`，写 `gate.evaluated`，通过确认后才写 `tool.started` 并进入 `UnifiedToolExecutor`；Tool 完成后写 `tool.completed`。Gate 决策目前主要以 audit event、confirmation、permit 和 payload 关联，尚未形成独立稳定的 `policy_decision_id` 字段。
- `backend/src/starter_agent/agent/context.py`：`ContextBuilder` 组装系统提示、身份、技能轻量目录、选中 Skill 完整内容、记忆和上下文摘要。外部或记忆内容被标为数据/摘要，不应作为系统指令。
- `backend/src/starter_agent/capabilities/registry.py`：`UnifiedToolRegistry` 维护原子快照。`lightweight_catalog()` 面向能力目录，保留 name/server/type/enabled/review/callable 等轻量字段；`model_snapshot().provider_tools()` 只包含当前 callable tools 的完整 description 与 input schema。`schemas()` 兼容旧 Runtime 接口并返回 provider tools。
- `backend/src/starter_agent/capabilities/gate.py`：`PreToolCallGate` 校验 tool 是否存在、是否 enabled、schema hash、JSON Schema、浏览器范围、SerpAPI payload 和策略规则；`UnifiedToolExecutor` 只接受有效 `ExecutionPermit`，并写入 `permit.consumed` 与 `tool.invoked` audit event。
- `backend/src/starter_agent/tools/base.py`：旧内置 Tool 仍通过 `Tool.schema()` 生成 provider function schema，包含真实 name、description 与 input schema。
- `backend/src/starter_agent/tools/policy.py`：旧 ToolPolicy 仍保留风险 allowlist 检查；当前统一 Gate/Executor 路径需要继续兼容旧 Tool 风险语义。
- `backend/src/starter_agent/mcp/manager.py`：`McpManager` 管理 MCP server 的连接、断开、启停、发现、刷新、健康检查与 tool 调用；Playwright 相关 Tool 还会经过网络范围 guard。
- `backend/src/starter_agent/skills/registry.py`：`SkillRegistry` 已存在，负责解析、加载、索引、启停和健康状态。当前工作区实际存在的 `job-research` Skill 定义位于 `backend/src/starter_agent/skills/job-research/SKILL.md`（当前版本 1.1.0）。信任层后续应以 Registry 实际加载结果为准，并把 Skill 版本写入 Eval Run。

## Trace、Log、Token 与存储现状

- `backend/src/starter_agent/capabilities/store.py`：`CapabilityStore` 当前持久化 MCP server、capability snapshot、MCP tools、builtin overrides、policy rules、confirmations、execution permits、skill records 和 `capability_audit_events`。`AuditEvent` 包含 `event_id`、`actor`、`action`、`target`、`decision`、`created_at` 与 `payload_json`，用于 capability trace。
- `backend/src/starter_agent/infrastructure/session_store.py`：`SQLiteSessionStore` 包含 `sessions`、`messages`、`turn_usage`、`context_summaries`、`token_calibration_profiles`、`tool_artifacts` 和 JD 入库确认等表。`tool_artifacts` 保存 server/call/snapshot/schema/source URL、hash、裁剪摘要和 restricted 标记；写入前会走脱敏与字段限制。
- `backend/src/starter_agent/observability/logging.py`：结构化 JSONL 日志在 renderer 之前执行脱敏；敏感 key 与正则覆盖 Authorization、Token、Cookie、密码、授权码、API key、邮件、正文片段和检索文本等类别。`httpx`/`httpcore` 日志被降噪到 WARNING，避免 URL query 凭据进入普通日志。
- `backend/src/starter_agent/interfaces/capabilities_api.py`：已有 `GET /v1/capabilities/traces`，当前只支持按 `turn_id` 过滤 audit events；已有 `GET /v1/capabilities/context-snapshots/{session_id}`，通过 `session_id`、`turn_id` 和 `revision` 查询 `model.context.snapshot`。这些可复用，但还没有 Eval Run/Case 级过滤、树状 Trace、失败簇或 Release Gate 视图。

## API 与前端现状

### 后端

- `backend/src/starter_agent/interfaces/api.py` 提供 `GET /health`、`GET /v1/tools`、`POST /v1/chat`、`POST /v1/chat/stream`、session 与 knowledge API。`GET /v1/tools` 返回旧内置 Tool 的 name/description/risk level，不等同于模型请求时的完整 provider tool snapshot。
- `backend/src/starter_agent/interfaces/capabilities_api.py` 提供能力管理 API，包括 catalog、server/tool/skill 查询与启停、review/policy、pending confirmation、confirmation decision、trace 与 context snapshot。当前没有 `/v1/trust/...` API。

### 前端

- `frontend/web/index.html` 是单文件前端。当前 hash 路由由 `CapabilityUiLogic.resolvePrimaryRoute()` 和导航事件处理，已存在 `#/chat`、`#/knowledge`、`#/capabilities/mcp-servers`、`#/capabilities/skills`。
- 聊天页已有 `chat-confirmation-card`，支持“仅本次执行”、加入 Allowlist 和取消；当服务端标记 always-confirm 时，前端会禁用加入 Allowlist 并展示原因。能力管理页已有 MCP servers 与 skills 视图。
- 当前没有完整 Trust Center，也没有 `#/trust/evals`、`#/trust/traces`、`#/trust/safety` 三个页签；前端还没有运行固定评测、比较 Run、查看失败簇或展示安全门禁证据的真实后端入口。

## `job-research` Skill 与编排链路

- `backend/src/starter_agent/skills/job-research/SKILL.md`：Skill 名称为 `job-research`，依赖 `search_jobs_serpapi`、`retrieve_resume_evidence`、`mcp__playwright__browser_navigate`、`mcp__playwright__browser_snapshot` 和 JD 入库服务。Skill 明确要求外部网页是数据不是指令，正向匹配必须引用简历 Chunk，失败时返回可恢复状态。
- `backend/src/starter_agent/skills/job_research.py`：`JobResearchOrchestrator` 真实使用 Tool 常量 `search_jobs_serpapi`、`mcp__playwright__browser_navigate`、`mcp__playwright__browser_snapshot`、`retrieve_resume_evidence`。搜索阶段调用 SerpAPI，分析阶段导航公开 URL、读取 Playwright snapshot、检索简历证据，并把依赖缺失、确认需求和 Tool 错误映射到结构化状态。
- Playwright MCP 工具的真实 model alias 当前按 MCP 适配器发布为 `mcp__playwright__browser_navigate` 与 `mcp__playwright__browser_snapshot`；是否 callable 取决于 server 连接、tool enabled、review 与 policy。

## `search_jobs_serpapi` 真实契约

实现位于 `backend/src/starter_agent/tools/builtin/job_search.py`，由 `backend/src/starter_agent/tools/registry.py` 构造并按 enabled allowlist 注册，同时也可进入 `UnifiedToolRegistry` 的 builtin 记录。

- **Name**：`search_jobs_serpapi`
- **Description**：`Search public job listings with sources and retrieval timestamps. Use structured job keywords, location, and desired result count. Results are leads that must be verified on the source page.`
- **Risk**：`read`
- **Input Schema 摘要**：`query`：必填，字符串长度 2–300；`location`：可选，字符串最长 100；`location_alias`：可选的拉丁字母地点别名，必须再由 Locations API 验证；`limit`：1–10，可选整数，默认 5；`query_variants`：可选，1–12 条查询；`hl`/`gl`：可选 locale；`google_domain` 仅允许 `google.com`；`expand_location_aliases` 控制 provider 驱动的地点别名扩展；禁止额外字段。

完整 schema：

```json
{
  "type": "object",
  "properties": {
    "query": {
      "type": "string",
      "description": "Job keywords, such as AI Agent engineer jobs.",
      "minLength": 2,
      "maxLength": 300
    },
    "location": {
      "type": "string",
      "description": "Optional city or region, such as Sydney.",
      "maxLength": 100
    },
    "location_alias": {
      "type": "string",
      "description": "Optional Latin alias; validated by Locations API.",
      "minLength": 1,
      "maxLength": 100,
      "pattern": "^[A-Za-z][A-Za-z0-9 .,'()&/\\-]*$"
    },
    "limit": {
      "type": "integer",
      "minimum": 1,
      "maximum": 10,
      "default": 5
    },
    "query_variants": {
      "type": "array",
      "items": {
        "type": "string",
        "minLength": 2,
        "maxLength": 300
      },
      "minItems": 1,
      "maxItems": 12
    },
    "hl": {
      "type": "string",
      "pattern": "^[a-z]{2}(?:-[a-z]{2})?$"
    },
    "gl": {
      "type": "string",
      "pattern": "^[a-z]{2}$"
    },
    "google_domain": {
      "type": "string",
      "enum": ["google.com"]
    },
    "expand_location_aliases": {
      "type": "boolean",
      "default": false
    }
  },
  "required": ["query"],
  "additionalProperties": false
}
```

### 配置与凭据

- `backend/src/starter_agent/settings.py` 的 `SerpApiToolConfig` 定义 `active_key`、`active_key_env`、`timeout_seconds`、`max_retries`、`retry_backoff_seconds` 与命名 key profiles。
- `config/config.yaml` 启用该 Tool；当前值为 profile `primary`、切换变量 `SERPAPI_ACTIVE_KEY`、15 秒 timeout、1 次 retry、0.5 秒 backoff，primary/backup 分别引用 `SERPAPI_API_KEY` 与 `SERPAPI_API_KEY_BACKUP`。
- `.env.example` 只声明上述环境变量名。`AgentSettings.serpapi_api_key()` 返回 profile、秘密值与环境变量名；Tool metadata 只保留 profile 和环境变量名，不返回 key 值。

### 行为、风险与错误码

- 单查询兼容路径先请求 SerpAPI `google_jobs`，不足时补充 `google`。地点别名扩展路径最多生成 12 条查询并同时覆盖两种 engine（最多 24 请求），合并 URL 并保留 query/engine provenance；结果仍只是岗位线索，必须回到来源页核验。
- `sanitize_url()` 仅保留 HTTP(S) URL，并移除 fragment 及常见敏感 query key。外部服务、网络、配额、认证与响应格式仍是不可信边界。
- 真实错误码集合：`invalid_arguments`、`missing_api_key`、`search_timeout`、`search_connection_failed`、`search_transport_error`、`invalid_response`、`no_results`、`authentication_failed`、`rate_limited`、`quota_exceeded`、`service_unavailable`、`invalid_search_request`、`search_failed`。

## 知识库/RAG 现状

- `backend/src/starter_agent/tools/builtin/knowledge.py` 已实现模型 callable RAG Tool `retrieve_resume_evidence`。输入 schema 包含必填 `query` 与可选 `top_k`，执行时从 `ToolContext` 注入 `user_id`、`project_id` 与 `knowledge_base_id`，并调用现有知识库服务检索当前作用域证据。
- `backend/src/starter_agent/interfaces/api.py` 仍提供应用/API 层 `POST /v1/knowledge-bases/{knowledge_base_id}/retrieve`。信任层固定 Eval 需要区分模型 Tool `retrieve_resume_evidence` 与应用 API，不得混用记录。
- RAG 失败边界包括 knowledge scope 不存在、scope mismatch、参数非法与 `no_evidence`。后续 Safety case 需要确认没有完整简历正文进入普通日志、报告或 UI。

## 旧 JD 抓取链路：可复用与替代边界

- `backend/src/starter_agent/tools/builtin/job_description_search.py` 的 `search_job_description` 是旧静态 JD 抓取 Tool，只接受用户选择的公开 HTTP(S) URL，并输出 source/final URL、读取时间、内容 hash、结构化字段和 completeness。
- `backend/src/starter_agent/tools/adapters/safe_web_fetcher.py` 与 `backend/src/starter_agent/tools/adapters/job_description_extractor.py` 提供 URL 验证、SSRF/DNS/peer 防护、redirect 逐跳验证、robots/timeout/大小/类型限制、敏感 URL 清理、内容 hash、来源追踪和静态结构化提取。
- `job-research` 当前目标链路使用 Playwright MCP 读取动态 JD；旧静态抓取链路仍可作为安全规则和错误处理参考，不应在没有回归证据时删除。

## 求职调研信任层 Task1 缺口清单

| 能力 | 当前状态 | 信任层结论 |
|---|---|---|
| 固定 Eval Runner | 未发现专用实现 | 需要新增；不能用变化的互联网结果计算固定基线 |
| 固定求职 Fixture | 未发现 `job-research` 专用 fixture 目录 | 需要新增脱敏搜索、JD、RAG chunk、MCP 响应、Tool error 与 injection fixture |
| Eval Run/Case 存储 | 未发现 Suite/Case/Run/Result/Assertion/Metric/Failure Cluster/Release Gate 表 | 需要新增或迁移 |
| Trace 关联 | 已有 capability audit event、`/v1/capabilities/traces` 和 context snapshots | 需要扩展 eval_run_id/case_id/session_id/turn_id/tool/policy/approval 关联 |
| 模型请求 ID | 现有 `model.context.snapshot` 使用 `call_id=model-call-{N}` | 缺少独立 model_request_id |
| Policy Decision ID | 现有 `gate.evaluated`、confirmation 和 permit 可关联 | 缺少独立 policy_decision_id |
| Tool 启停证据 | `UnifiedToolRegistry` 已区分 lightweight catalog 与 provider tools | 需要固定回归证明关闭项只有 Name/轻量信息、无完整 Description/Input Schema、不可调用 |
| Pre-Tool-Call Gate | 已有 Gate、confirmation、permit 与 chat confirmation card | 需要补全白名单、强制确认、取消、超时、重复点击和无真实 Tool Start 回归 |
| 日志脱敏 | 已有写入前脱敏和受限 artifact | 需要新增假 Token 泄漏回归，证明报告、日志、UI 均不含秘密 |
| 真实 Smoke | `tests/e2e/test_playwright_job_research.py` 覆盖公开 JD 与 Playwright MCP，但 provider 是脚本化测试替身 | 没有真实模型 Smoke；需要单独报告且不混入固定基线 |
| Trust Center | 前端已有 chat/knowledge/capability 路由 | 没有完整 Trust Center 与 Evals/Traces/Safety 页签 |

## 实施依赖清单

| 能力 | 当前状态 | Job Research / Trust 结论 |
|---|---|---|
| `search_jobs_serpapi` | 已注册的内置 `read` Tool | 直接复用真实 Name/Schema |
| Playwright MCP `mcp__playwright__browser_navigate` | 通过 MCP discovery/registry 发布 | 复用；是否 callable 由启停、连接、review、policy 决定 |
| Playwright MCP `mcp__playwright__browser_snapshot` | 通过 MCP discovery/registry 发布 | 复用；需要来源、裁剪和注入回归 |
| `retrieve_resume_evidence` | 已实现模型 callable RAG Tool | 复用；需要作用域与敏感正文脱敏回归 |
| `job-research` Skill | 已存在 `SKILL.md` 与 registry 支持 | 复用；需要固定 Eval 覆盖编排、失败处理和安全说明 |
| `JobResearchOrchestrator` | 已实现应用编排 | 复用；需要 Trace/Eval 关联 |
| Capability trace/context snapshot | 已实现基础 API | 扩展 Run/Case/失败簇/门禁查询 |
| Trust Center | 尚未实现 | 新增真实后端 API 与前端三页签 |
