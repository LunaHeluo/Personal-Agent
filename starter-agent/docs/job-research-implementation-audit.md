# Job Research 实施前仓库审计

## 审计范围与结论

本审计以当前仓库代码为准，覆盖配置、应用组装、Tool 注册与执行、API、日志、前端、SerpAPI、知识库、Skill 现状和旧 JD 抓取链路。现有能力可以复用 `search_jobs_serpapi`、知识库应用服务，以及旧 JD 链路中的安全抓取和静态解析逻辑；模型可调用的 RAG Tool 与 Skill 子系统则必须新增。

## 配置加载与应用生命周期

- `src/starter_agent/settings.py`：`load_settings()` 先读取 `EnvironmentSettings.starter_agent_config`（环境变量名为 `STARTER_AGENT_CONFIG`），默认选择 `config/config.yaml`；相对路径按项目根目录解析，目标不存在时才尝试 `config/config.example.yaml`。文件使用 `yaml.safe_load()`，随后由 `AgentSettings.model_validate()` 校验 Provider、Runtime、Knowledge 和 Tools 等结构。
- 同一文件中的 `AgentSettings._environment_value()` 先读进程环境变量，再逐行读取项目根目录的 `.env`。`provider_api_key()`、`serpapi_api_key()` 和邮件配置只通过环境变量名解析秘密值；SerpAPI 的 active profile 可由 `SERPAPI_ACTIVE_KEY` 覆盖。
- `src/starter_agent/bootstrap.py`：`get_settings()`、`create_application()` 和 `create_knowledge_service()` 都使用 `@lru_cache`。`create_application()` 依次配置日志，构造 `SQLiteSessionStore`、`ProviderRegistry`、`ToolRegistry`、`ToolPolicy`、`AgentRuntime` 与 `ContextBuilder`；`create_knowledge_service()` 另行构造缓存的 `SQLiteKnowledgeStore` 和 `KnowledgeApplicationService`。
- `src/starter_agent/interfaces/api.py`：FastAPI lifespan `_api_lifespan()` 在关闭时等待 `create_application().wait_for_background_tasks()`；启动阶段没有额外初始化动作。缓存对象的创建发生在首次调用相应 bootstrap 函数时。

## Tool 注册、Schema 暴露与执行

- `src/starter_agent/tools/registry.py`：`ToolRegistry` 构造全部内置候选 Tool，再按 `settings.tools.enabled` 形成 `_tools`；未知名称会抛出 `ConfigurationError`。`list()`/`get()` 提供查询，`schemas()` 对启用 Tool 调用 `Tool.schema()`。
- `src/starter_agent/tools/base.py`：`Tool.schema()` 生成 Provider function schema，包含真实 `name`、`description` 和 `input_schema`；执行上下文 `ToolContext` 当前只有 `session_id` 与 `turn_id`。
- `src/starter_agent/tools/policy.py`：`ToolPolicy.check()` 只比较 Tool 的 `risk_level` 与配置的 allowlist。
- `src/starter_agent/agent/runtime.py`：每次 `Provider.complete()` 直接接收 `self.tools.schemas()`；模型返回 Tool Call 后，Runtime 按名称 `get()`、执行风险检查、用 `asyncio.wait_for()` 调用 `tool.execute()`，再将 `ToolResult` 经 `ToolResultGuard` 写回 Tool message。未知 Tool、策略拒绝、超时和未捕获异常分别映射为 `unknown_tool`、策略错误码、`tool_timeout` 和 `tool_execution_error`。
- 当前执行路径没有通用 JSON Schema validator。具体 Tool 自行校验参数；因此 schema 中的 `additionalProperties: false` 是模型侧契约，而 `search_jobs_serpapi` 的本地 `_validate_arguments()` 当前不会主动拒绝未知键。这是后续统一执行/Gate 实施时应保留的审计边界。

## API 与前端现状

### 后端

- `src/starter_agent/interfaces/api.py` 的 `GET /health` 只返回 `status=ok` 与应用名，不检查 Provider、Tool 或外部服务健康。
- `GET /v1/tools` 从运行中 `ToolRegistry.list()` 返回 `name`、`description`、`risk_level`；它不返回 input schema。模型侧 schema 由 `AgentRuntime` 直接读取 Registry。
- 聊天接口为 `POST /v1/chat` 与 `POST /v1/chat/stream`。流式接口使用 SSE，产生 `delta`、`tool_started`、`tool_completed`、`done` 或 `error` 事件；`knowledge_mode=required` 时走知识库回答分支。
- 现有知识库接口均位于同一文件：`GET /v1/knowledge-bases`；文档的 `POST/GET /v1/knowledge-bases/{knowledge_base_id}/documents`、`GET /v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}`、`GET .../chunks`、`PUT .../content`、`DELETE .../{document_id}`；以及 `POST /v1/knowledge-bases/{knowledge_base_id}/retrieve`、`POST .../answer`、`GET .../citations/{chunk_id}` 和 `GET .../ingestion-jobs/{job_id}`。

### 前端

- `src/web/index.html` 是单文件前端。主导航当前只有“对话”和“知识库”，`showPrimaryView()` 通过元素的 `hidden` 状态切换 `chatView`/`knowledgeView`；当前没有 hash 路由或“能力管理”主视图。
- `loadTools()` 请求 `/v1/tools`，将结果保存到 `state.tools`；输入 `/` 时依据真实 Tool name 过滤并显示菜单，选中后形成带 `tool` 字段的聊天请求。
- `sendMessage()` 请求 `/v1/chat/stream`。`readStream()` 用 `ReadableStream.getReader()`、`TextDecoder` 和 SSE 空行分帧解析 `data:`；`applyStreamEvent()`分别渲染 `tool_started`、`tool_completed`、`delta`、`done`、`error`。

## `search_jobs_serpapi` 真实契约

实现位于 `src/starter_agent/tools/builtin/job_search.py`，由 `src/starter_agent/tools/registry.py` 构造并按 enabled allowlist 注册。

- **Name**：`search_jobs_serpapi`
- **Description**：`Search public job listings with sources and retrieval timestamps. Use structured job keywords, location, and desired result count. Results are leads that must be verified on the source page.`
- **Risk**：`read`
- **Input Schema 摘要**：`query`：必填，字符串长度 2–300；`location`：可选，字符串最长 100；`limit`：1–10，可选整数，默认 5；禁止额外字段。

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
    "limit": {
      "type": "integer",
      "minimum": 1,
      "maximum": 10,
      "default": 5
    }
  },
  "required": ["query"],
  "additionalProperties": false
}
```

### 配置与凭据

- `src/starter_agent/settings.py` 的 `SerpApiToolConfig` 定义 `active_key`、`active_key_env`、`timeout_seconds`、`max_retries`、`retry_backoff_seconds` 与命名 key profiles。
- `config/config.yaml` 启用该 Tool；当前值为 profile `primary`、切换变量 `SERPAPI_ACTIVE_KEY`、15 秒 timeout、1 次 retry、0.5 秒 backoff，primary/backup 分别引用 `SERPAPI_API_KEY` 与 `SERPAPI_API_KEY_BACKUP`。
- `.env.example` 只声明上述环境变量名。`AgentSettings.serpapi_api_key()` 返回 profile、秘密值与环境变量名；Tool metadata 只保留 profile 和环境变量名，不返回 key 值。

### 行为、风险与错误码

- Tool 先请求 SerpAPI `google_jobs`，无结果时回退 `google`；结果只是岗位线索，包含来源 URL 与 `retrieved_at`，snippet 最长 1000 字符，必须回到来源页核验。
- `sanitize_url()` 仅保留 HTTP(S) URL，并移除 fragment 及常见敏感 query key。`src/starter_agent/observability/logging.py` 同时把 `httpx`/`httpcore` 日志提升到 WARNING，避免带 query credential 的 SerpAPI URL 在 INFO 日志泄漏。
- 外部服务、网络、配额、认证与响应格式仍是不可信边界。代码会按配置重试 transport error 与 5xx，但不会把搜索摘要视为完整 JD。
- 真实错误码集合：`invalid_arguments`、`missing_api_key`、`search_timeout`、`search_connection_failed`、`search_transport_error`、`invalid_response`、`no_results`、`authentication_failed`、`rate_limited`、`quota_exceeded`、`service_unavailable`、`invalid_search_request`、`search_failed`。

## 知识库/RAG 现状

- `src/starter_agent/interfaces/api.py` 已实现 `POST /v1/knowledge-bases/{knowledge_base_id}/retrieve`。`KnowledgeRetrieveRequest` 接收 `question`（1–10000）、`top_k`（1–50，默认 6）、`document_ids`、`document_types`、`filenames` 与 `versions`；响应为 `status`（`ok` 或 `no_evidence`）及 `matches`。
- `src/starter_agent/knowledge/service.py` 已实现 `KnowledgeApplicationService.retrieve()`，把缓存服务的固定 `KnowledgeScope`、knowledge base、问题和上述 filters 交给 `KnowledgeRetriever`，并把 top_k 限制到最多 50。
- 以上是应用/API 能力，不会经 `ToolRegistry` 暴露给模型。`src/starter_agent/tools/registry.py` 没有知识库 Tool，仓库中也没有对应实现：`retrieve_resume_evidence` 尚未实现。它是 job-research 所需的待新增模型 callable RAG 适配器，不得把现有 `/retrieve` API 误记为模型 Tool，也不得用文件式简历 Tool 冒充 RAG。

## Skill 现状

对当前项目的 `SKILL.md`、skills 目录、Python/YAML 中的 Skill Registry、parser、selector 与 trigger 机制进行扫描，未发现运行时实现。当前没有 `skills/`、没有 `SKILL.md`、没有 Skill 解析器或触发机制，且 **Skill Registry 尚未实现**。因此 `job-research` Skill 与其依赖状态、解析、选择、启停和触发都属于待新增子系统，不能作为现有能力调用。

## 旧 JD 抓取链路：可复用与替代边界

### 现有链路

- `src/starter_agent/tools/builtin/job_description_search.py` 的 `search_job_description` 是 `read` Tool，只接受一个用户选择的公开 HTTP(S) URL，并可携带 `expected_title`、`expected_company` 和 `source_ref`。它拒绝额外字段、列表页、空/不完整静态内容与岗位不匹配，输出 source/final URL、读取时间、内容 hash、结构化字段和 completeness。
- `src/starter_agent/tools/adapters/safe_web_fetcher.py` 的 `SafeWebFetcher` 负责安全读取：只允许 HTTP(S)，拒绝凭据、fragment、非标准端口、本地/metadata hostname、非公网或混淆 IP；DNS 后固定选定地址并校验实际 peer；每次 redirect 都重新验证。生产 client 禁用环境代理（`trust_env=False`），手动处理 redirect，并实施全程 timeout、robots、状态码、内容类型/压缩、声明长度和流式字节上限检查。公开 URL 会移除敏感 query 值，结果带 SHA-256。
- `src/starter_agent/tools/adapters/job_description_extractor.py` 的 `JobDescriptionExtractor` 把外部内容作为惰性数据处理，支持 JobPosting JSON-LD、HTML 和纯文本；去除 script/style/nav 等噪声，提取职责、要求、加分项和福利，计算 `complete`/`partial`/`unverified`。JSON-LD 遍历还设置节点数与深度上限。

### 实施边界

- 上述 URL 验证、SSRF/DNS/peer 防护、redirect 逐跳验证、robots/timeout/大小/类型限制、敏感 URL 清理、内容 hash、来源追踪和静态结构化提取均可作为新浏览器/MCP 治理与结果规范化的参考或复用逻辑。
- `search_job_description` 依赖自建静态 HTTP 抓取，无法执行动态页面脚本、登录或绕过访问控制；它对动态或不完整页面返回失败。这正是 Playwright MCP 读取能力要覆盖的边界，但浏览器能力在真实端到端覆盖与回归验证前不能宣称替代现有 Tool。
- 本阶段不删除、不停用 `search_job_description`、`SafeWebFetcher` 或 `JobDescriptionExtractor`。任何后续删除必须建立在新能力完整替代的证据和单独确认之上。

## 实施依赖清单

| 能力 | 当前状态 | Job Research 结论 |
|---|---|---|
| `search_jobs_serpapi` | 已注册的内置 `read` Tool | 直接复用真实 Name/Schema |
| 知识库 `/retrieve` 与 `KnowledgeApplicationService.retrieve()` | 已实现应用/API 能力 | 复用服务层 |
| `retrieve_resume_evidence` | 尚未实现 | 新增模型 callable RAG Tool |
| Skill Registry 与 `job-research` Skill | 尚未实现 | 新增 Skill 子系统 |
| `search_job_description` 安全抓取/解析 | 已实现但动态站点能力有限 | 保留并复用安全规则；替代前验证 |
| 前端能力管理视图/路由 | 尚未实现 | 基于现有 `showPrimaryView()` 扩展时不得假设已有路由 |
