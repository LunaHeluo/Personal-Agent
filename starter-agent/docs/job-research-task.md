# Starter Agent 求职调研外部能力实施任务

> 本文只定义实施拆分。实际执行进度由任务管理机制记录，不在本文维护。

**目标：** 为 Starter Agent 建立可治理的 MCP/Skill 能力平台，并用 SerpAPI、Playwright MCP 和知识库 RAG 完成带来源的求职调研闭环。

**架构基线：** 每个 MCP Server 由独立 Client 连接管理；动态能力使用版本化不可变快照；所有真实 Tool 调用统一经过 `PreToolCallGate`；关闭能力只进入轻量目录；聊天确认、管理操作和 Tool Trace 均由后端持久化并审计。

**技术基线：** Python 3.11/3.12、FastAPI、Pydantic、SQLAlchemy/SQLite、官方 Python MCP SDK、stdio、structlog、现有单文件 Web 前端、pytest。

## 全局执行约束

- Browser MCP 必须从独立 JSON 配置加载以下真实 Server 定义，不得替换为 Mock 或硬编码 Tool 列表：

```json
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": [
        "@playwright/mcp@latest"
      ]
    }
  }
}
```

- 不得让 MCP Client、Skill、重试逻辑、管理测试接口或前端绕过 `PreToolCallGate`。
- 未连接、禁用或未通过审查的 Tool 不得进入模型 callable tools，也不得向模型注入完整 Description/Input Schema。
- 强制人工确认动作不能通过长期 allowlist 绕过。
- Browser 不得登录、投递、上传简历、发送消息或绕过 robots、验证码、付费墙和访问控制。
- 每个 Task 先写失败测试，再实现最小能力，再运行本 Task 测试和相关回归。
- 不得以配置存在、代码写完、Mock 通过、单元测试通过或模型声称成功作为整体完成依据。
- 只有 Task16 的真实公开 JD 端到端链路通过后，才能宣称该能力满足总体验收。
- 每个 Task 交付时汇报修改文件、实际测试命令与结果、逐项验收结论和剩余风险。

## Task1：审计现有能力与确定实施边界

### 任务目标

形成可复核的仓库现状基线，确认哪些能力可以复用、哪些必须新增，防止实施期间臆造 Tool、Skill、CLI 或前端路由。

### 子任务

1. 阅读并记录以下真实链路：
   - `src/starter_agent/settings.py` 的 YAML 加载与环境变量解析。
   - `src/starter_agent/bootstrap.py` 的应用组装与缓存生命周期。
   - `src/starter_agent/tools/registry.py`、`tools/base.py`、`tools/policy.py` 和 `agent/runtime.py` 的 Tool 注册、Schema 暴露与执行路径。
   - `src/starter_agent/interfaces/api.py` 的 `/health`、`/v1/tools`、聊天、知识库接口和 FastAPI lifespan。
   - `src/starter_agent/observability/logging.py` 的 structlog 与脱敏规则。
   - `src/web/index.html` 的主视图切换、Tool 列表和流式聊天事件。
2. 将 `search_jobs_serpapi` 的真实 Name、Description、Input Schema、风险、配置与错误码记录到 `docs/job-research-implementation-audit.md`。
3. 记录知识库现有 `/retrieve` API 和 `KnowledgeApplicationService.retrieve()`；明确模型 callable 的 `retrieve_resume_evidence` 尚不存在。
4. 扫描项目 Skill 目录、`SKILL.md`、解析器与触发机制；明确当前不存在 Skill Registry。
5. 记录旧 `search_job_description`、`SafeWebFetcher` 和 `JobDescriptionExtractor` 的可复用安全逻辑与待替代边界，不做删除。
6. 为审计文档增加自动一致性测试 `tests/unit/test_job_research_audit.py`，检查关键真实名称、Schema 摘要和“尚未实现”依赖标记。

### 依赖关系

依赖已确认的 `docs/job-research-requirements.md` 和 `docs/job-research-design.md`，不依赖其他 Task。

### 验收标准

- 审计文档能定位配置、Registry、Runtime、健康、日志、前端、SerpAPI、RAG 和 Skill 现状的真实文件。
- `search_jobs_serpapi` Schema 与代码一致：`query` 必填，`location` 可选，`limit` 为 1–10，禁止额外字段。
- 文档明确说明 RAG callable Tool 和 Skill 系统是待新增依赖。
- 不包含仓库中不存在的命令、接口或 Tool 名称。
- `uv run pytest tests/unit/test_job_research_audit.py -q` 返回 0 failures。

### 预估复杂度

低。主要风险是把已有知识库 API 误写成模型 Tool，或遗漏旧 JD 抓取能力的重叠范围。

## Task2：建立 MCP 配置模型、治理数据模型与持久化

### 任务目标

为独立 MCP JSON、Server/Tool 生命周期、能力快照、策略、确认和审计提供可验证的数据契约与 SQLite 持久化基础。

### 子任务

1. 在 `pyproject.toml` 增加官方 Python MCP SDK 和 JSON Schema 校验运行时依赖，并更新锁文件。
2. 在 `src/starter_agent/settings.py` 增加 `McpSettings`，包含 `config_path`、初始化/健康/调用/关闭/确认超时；在 `config/config.example.yaml` 和 `config/config.yaml` 增加 `mcp.config_path: config/mcp.json`。
3. 新增 `config/mcp.json` 与 `config/mcp.example.json`，二者包含要求中的 `playwright` stdio 定义；不得保存 Secret。
4. 新增 `src/starter_agent/mcp/config.py`，实现独立 JSON 解析、项目根路径解析、command/args/cwd/env-name 校验和规范化配置 hash。
5. 新增 `src/starter_agent/capabilities/models.py`，定义 Server、Snapshot、Tool、Resource、Prompt、PolicyRule、Confirmation、AuditEvent、ExecutionPermit 和 SkillRecord 的 Pydantic 领域模型。
6. 新增 `src/starter_agent/capabilities/store.py`，在现有 SQLite 数据库创建 additive 表，并实现 revision 条件更新、快照激活、策略变更、确认幂等更新和审计追加。
7. 新增 `tests/unit/test_mcp_config.py`、`test_capability_models.py`、`test_capability_store.py`，覆盖路径逃逸、内联 Secret 拒绝、配置 hash 稳定性、revision 冲突和确认重复提交。

### 依赖关系

依赖 Task1 的真实现状和命名基线。

### 验收标准

- Starter Agent 能从主 YAML 找到独立 MCP JSON，并解析出 `playwright/npx/@playwright/mcp@latest`。
- 相同规范化配置生成相同 hash；配置变化生成不同 hash。
- 内联 API Key、Token、Cookie 或密码配置被拒绝。
- SQLite 能持久化 Server、快照、Tool、规则、确认、Skill 和审计，重启后可读取。
- 并发 revision 冲突不会静默覆盖较新的管理操作。
- `uv run pytest tests/unit/test_mcp_config.py tests/unit/test_capability_models.py tests/unit/test_capability_store.py -q` 返回 0 failures。

### 预估复杂度

高。数据契约会被后续所有 Task 复用，字段与 revision 语义需要一次确定。

## Task3：实现独立 MCP Client 生命周期与 Playwright stdio 启动

### 任务目标

使用官方 MCP SDK为每个 Server 建立独立 Client/Session，真实启动 Playwright MCP，并正确处理初始化、stderr、退出、超时和关闭。

### 子任务

1. 新增 `src/starter_agent/mcp/client.py`，封装 `StdioServerParameters`、`stdio_client`、`ClientSession` 和 `AsyncExitStack`，禁止手写 JSON-RPC framing。
2. 新增 `src/starter_agent/mcp/manager.py`，实现 `McpClientManager` 与每 Server `ServerHandle`：独立 Session、connect lock、refresh lock、在途计数、drain event 和超时。
3. 连接前检查 Node.js 与 npx 是否可解析，并记录非敏感版本摘要；检查失败映射为 `node_not_found` 或 `npx_not_found`。
4. 使用 `command=npx`、`args=["@playwright/mcp@latest"]` 建立 stdio；stdout 仅供协议，stderr 使用限长环形缓冲并经过日志脱敏。
5. 在超时内执行 MCP initialize，记录协议版本和 `serverInfo.name/version`；缺少版本时写入 `unknown`，不得以 `@latest` 替代实际版本。
6. 监听子进程结束并记录退出码、启动时间和安全 stderr 摘要；异常退出后 Client 进入不可用状态。
7. 将 Manager 接入 `src/starter_agent/bootstrap.py` 与 `interfaces/api.py` 的 FastAPI lifespan：启动时连接启用 Server，关闭时停止接收新调用、drain 并关闭 Session/子进程。
8. 新增 `tests/unit/test_mcp_client_lifecycle.py` 与 `tests/integration/test_mcp_manager_lifecycle.py`，使用受控测试 MCP Server 验证独立连接、初始化超时、退出码和关闭；此测试不替代真实 Playwright 验收。

### 依赖关系

依赖 Task2 的 MCP 配置、模型和 Store。

### 验收标准

- 两个测试 Server 使用不同 ClientSession、锁和生命周期，关闭一个不影响另一个。
- 初始化超时会关闭候选 stdio 进程且不泄漏句柄。
- stderr 不参与协议解析，敏感字段不进入日志。
- 应用 shutdown 在预算内 drain 并关闭全部 Session；超时路径有稳定错误码。
- 运行时版本来自 initialize result 并可从 Manager 查询。
- `uv run pytest tests/unit/test_mcp_client_lifecycle.py tests/integration/test_mcp_manager_lifecycle.py -q` 返回 0 failures。

### 预估复杂度

高。重点是 asyncio 上下文生命周期、Windows 子进程行为和异常关闭回收。

## Task4：实现健康检查、能力发现与版本化快照

### 任务目标

初始化后真实发现 Tools、Resources、Resource Templates 和 Prompts，校验 Schema并发布不可变能力快照。

### 子任务

1. 新增 `src/starter_agent/mcp/discovery.py`，分页调用 SDK 的 list tools/resources/resource templates/prompts。
2. 规范化 Tool Name、Title、Description、Input Schema、annotations、读写提示和 Schema hash；保留真实 upstream name。
3. 为模型侧生成稳定别名 `mcp__{server_id}__{upstream_name}`，检测与内置 Tool 或其他 MCP Tool 的冲突。
4. 将候选发现结果写入 `mcp_capability_snapshots` 及相关能力表，全部校验成功后一次激活。
5. 默认把新发现 Tool 标记为未审查、禁用且不在 allowlist；只在管理视图展示完整 Schema。
6. 实现协议 Ping 健康检查，不通过业务 Tool 探活；分别记录连接与健康生命周期。
7. 扩展 structlog 事件：连接、初始化、发现数量、版本、Schema hash、健康检查、候选失败；不记录 Schema 全文。
8. 新增 `tests/unit/test_mcp_discovery.py`、`test_mcp_health.py` 和 `tests/integration/test_capability_snapshot.py`。

### 依赖关系

依赖 Task3 的独立 ClientSession 和 Task2 的快照 Store。

### 验收标准

- 发现结果来自测试 MCP Server 的真实协议响应，不是硬编码列表。
- Tools/Resources/Prompts 数量、版本和 Schema hash 可从 Store 与 Manager 查询。
- 任一能力 Schema 无效时不激活半成品快照。
- 新 Tool 默认不会出现在模型 callable tools。
- Ping 失败不会误称 Server 已断开；连接与健康信息可区分。
- `uv run pytest tests/unit/test_mcp_discovery.py tests/unit/test_mcp_health.py tests/integration/test_capability_snapshot.py -q` 返回 0 failures。

### 预估复杂度

中高。难点是分页、动态 Schema规范化和候选快照原子发布。

## Task5：实现单 Server 刷新与隔离回滚

### 任务目标

刷新指定 Server 的配置、连接、版本、能力与健康，而不影响其他 Server 或运行中调用。

### 子任务

1. 在 `McpClientManager` 实现 `refresh_server(server_id, expected_revision)`，使用目标 Server 独立 refresh lock。
2. 同 Server 并发刷新返回 `refresh_in_progress`；不同 Server 允许并行刷新。
3. 为目标 Server 启动候选 Client，完成 initialize、发现和快照校验后原子交换；交换前不关闭当前 Client。
4. 运行中调用绑定开始时的 `snapshot_id` 和 Client lease；交换后新调用只绑定新快照。
5. 旧 Client 进入 drain，调用结束或超时后关闭。
6. 候选失败时保留当前 Client，将旧快照标记 stale 并记录错误；不得影响其他 Server。
7. Schema hash 变化时撤销对应 Tool 的自动执行资格、使旧确认和 Permit 失效，并要求重新审查。
8. 新增 `tests/unit/test_mcp_refresh_state_machine.py` 和 `tests/integration/test_mcp_refresh_isolation.py`，覆盖成功交换、失败回滚、并发、在途调用和其他 Server 隔离。

### 依赖关系

依赖 Task4 的发现/快照和 Task3 的 Client lease/drain。

### 验收标准

- 刷新只操作目标 Server 的 Client、能力和错误记录。
- 在途调用使用旧快照完成，新调用使用新快照。
- 刷新失败后旧能力仍可查询，且明确显示 stale 与最近错误。
- Schema 变化后自动执行规则不会继续生效。
- 两个 Server 的刷新和调用不会因全局锁互相阻塞。
- `uv run pytest tests/unit/test_mcp_refresh_state_machine.py tests/integration/test_mcp_refresh_isolation.py -q` 返回 0 failures。

### 预估复杂度

高。涉及双 Client 切换、引用计数、原子快照与故障回滚。

## Task6：实现 Server/Tool 启停与两级 Context 暴露

### 任务目标

把内置 Tool 和动态 MCP Tool 组合成原子 Registry 快照，严格分离轻量能力目录与模型 callable tools。

### 子任务

1. 新增 `src/starter_agent/capabilities/registry.py`，实现 `UnifiedToolRegistry`、`LightweightCapabilityCatalog` 和 `ModelToolSnapshot`。
2. 内置 Tool 继续由现有 `ToolRegistry` 构建；MCP Tool 通过 Adapter 注册，不复制现有 Tool 实现。
3. 轻量目录只包含 name、server、type、enabled/review 概要，不包含 Description、Input Schema 或 Output Schema。
4. callable snapshot 只包含 Server 已连接且启用、Tool 已启用且通过暴露审查的完整模型 Tool 定义。
5. 每次 Server/Tool 启停、审查、刷新或策略变化递增 `context_revision` 并原子替换快照引用。
6. 修改 `src/starter_agent/agent/runtime.py`，在每次 `provider.complete()` 前读取一次最新 snapshot，并将 revision 写入 Trace；不得复用启动时 Schema 列表。
7. 修改 `/v1/tools` 保持现有兼容输出，同时增加来源与可调用概要；完整 Schema 只由管理 API 返回。
8. 新增 `tests/unit/test_capability_registry.py`、`test_context_tool_exposure.py` 和 `tests/integration/test_model_tool_snapshot.py`。

### 依赖关系

依赖 Task4 的能力快照和 Task2 的启停/审查持久化。

### 验收标准

- 关闭 Server 或 Tool 后，下一轮模型请求不含其完整 Name/Description/Input Schema。
- 关闭项只在轻量目录保留名称与禁用信息。
- 重新启用但未审查的 Tool 仍不进入 callable tools。
- 启用且审查通过的 Tool 才恢复完整 Schema。
- snapshot 交换对并发模型请求是原子的，不出现混合 revision。
- `uv run pytest tests/unit/test_capability_registry.py tests/unit/test_context_tool_exposure.py tests/integration/test_model_tool_snapshot.py -q` 返回 0 failures。

### 预估复杂度

高。该 Task 改变模型 Context 的权威来源，必须保持现有内置 Tool 兼容。

## Task7：实现 Browser 策略、allowlist 与 PreToolCallGate

### 任务目标

在所有真实 Tool 执行入口前建立统一授权决策，支持自动允许、等待确认和拒绝，且无法被旁路。

### 子任务

1. 新增 `src/starter_agent/capabilities/policy.py`，实现 Server/Tool/action/scheme/domain/参数/数据分类范围的规则匹配。
2. 定义优先级：`deny/disabled > always_confirm > allowlist_auto > confirm_once > require_confirmation`。
3. Browser 初始协议范围为 HTTP(S)，域名通配；仍拒绝 URL 凭证、私网/localhost/链路本地/云元数据目标和禁止动作。
4. 按真实发现 Tool Schema 完成风险审查：读取/快照、导航、点击、输入、上传、登录、提交、脚本、下载和存储写入分别分类。
5. 登录、投递、消息、上传简历和绕过访问控制直接 deny；脚本、下载、存储写入和其他副作用 always_confirm。
6. 新增 `src/starter_agent/capabilities/gate.py`，实现 `PreToolCallGate.evaluate(request) -> allow | require_confirmation | deny`，检查启停、snapshot/schema hash、JSON Schema 参数、范围、角色、规则、外发数据、预算与重复调用。
7. 只有 Gate 生成的短期 `ExecutionPermit` 才能调用 `UnifiedToolExecutor`；修改内置 Tool 和 MCP Tool 执行路径拒绝无 Permit 调用。
8. 管理页测试、Skill、重试和用户指定 Tool 都调用同一 Executor，不提供直接 Client 路由。
9. 新增 `tests/unit/test_tool_policy_rules.py`、`test_pre_tool_call_gate.py`、`test_browser_scope_policy.py` 和 `tests/integration/test_gate_no_bypass.py`。

### 依赖关系

依赖 Task6 的 Registry snapshot 和 Task2 的 PolicyRule/Permit Store。

### 验收标准

- Gate 对同一请求稳定返回 allow、require_confirmation 或 deny，并包含安全原因。
- 未启用、Schema 不匹配、域名越界、禁止动作和敏感外发数据均在真实 Tool 请求前被阻止。
- allowlist 普通 Tool 可自动执行，always_confirm 不被 allowlist 覆盖。
- 任何没有 Permit 的 MCP Client 或内置 Tool 执行请求被拒绝。
- 简历原文不会外发给 Browser；SerpAPI 只收到岗位关键词和地点。
- `uv run pytest tests/unit/test_tool_policy_rules.py tests/unit/test_pre_tool_call_gate.py tests/unit/test_browser_scope_policy.py tests/integration/test_gate_no_bypass.py -q` 返回 0 failures。

### 预估复杂度

高。权限优先级、动态 Schema 与所有执行入口收口是核心安全边界。

## Task8：实现持久化确认、聊天暂停与审计

### 任务目标

当 Gate 要求确认时持久化待确认记录，暂停当前 Tool Call，并在用户决定后重新校验和一次性执行。

### 子任务

1. 新增 `src/starter_agent/capabilities/confirmations.py`，实现 `ConfirmationService` 与内存 `ConfirmationBroker`。
2. `require_confirmation` 先写 SQLite，再产生聊天事件；记录 Server、Tool、参数 hash/安全摘要、风险、数据去向、schema hash、策略 revision 和过期时间。
3. 修改 `AgentRuntime` 与 `ApplicationService`，通过 `TurnCoordinator` 在当前 Tool Call 处异步等待，不占用线程且不提前调用 Tool。
4. 实现决定动作：`once`、`allowlist`、`cancel`；前端文案对应「仅本次执行」「执行并加入白名单」「取消」。
5. `once` 生成绑定 principal/session/turn/call/tool/schema/参数/数据范围的一次性 Permit；`allowlist` 仅为可持久放行 Tool 建规则。
6. 用户决定后重新运行 Gate；Server/Tool/Schema/参数/策略变化使决定失效。
7. 使用 idempotency key 和条件更新防重复点击；处理超时、用户取消、连接断开和并发决定。
8. 页面刷新可通过查询接口恢复 Pending；服务进程重启后旧 Pending 进入 expired，禁止自动执行。
9. 为确认创建、决定、失效、Permit 消费和真实调用写安全审计事件。
10. 新增 `tests/unit/test_tool_confirmations.py`、`test_confirmation_broker.py` 和 `tests/integration/test_confirmation_execution_barrier.py`。

### 依赖关系

依赖 Task7 的 Gate/Permit 和 Task2 的 Confirmation/Audit Store。

### 验收标准

- 确认前、取消后和超时后 MCP Server 均未收到真实 Tool 请求。
- 仅本次 Permit 只能消费一次，参数或 Schema 变化后不可使用。
- 强制确认动作不能通过“加入白名单”自动放行。
- 页面刷新能恢复同一 Pending 卡片；重复决定只产生一次执行。
- 审计记录能关联 session、turn、call、规则、决定和最终执行。
- `uv run pytest tests/unit/test_tool_confirmations.py tests/unit/test_confirmation_broker.py tests/integration/test_confirmation_execution_barrier.py -q` 返回 0 failures。

### 预估复杂度

高。涉及持久化生命周期、异步恢复、幂等与执行屏障。

## Task9：实现 MCP Tool Adapter、Result 治理、Trace 与 JD 规范化

### 任务目标

把真实 MCP Tool Result 安全地接入现有 `ToolResult`/Context，保留来源、裁剪和可追溯 Artifact，并形成可验证 JD。

### 子任务

1. 新增 `src/starter_agent/mcp/tool_adapter.py`，将 MCP content blocks、错误标志和 upstream metadata 转成现有 `ToolResult`。
2. 扩展 `ToolResultGuard`：先脱敏再裁剪，记录 raw/kept 字节、字符、Token、hash、`is_truncated`、`truncation_reason` 和 `raw_source_ref`。
3. 扩展 `tool_artifacts`，保存受限且脱敏的原始结果、server、snapshot、schema hash、requested/final source URL 和裁剪摘要。
4. 保护来源 URL、content hash、call id、snapshot id 和裁剪标记，使其不因 Context 裁剪丢失。
5. 新增 `src/starter_agent/job_research/jd.py`，实现 `JobDescriptionNormalizer` 与字段级 source refs；无法验证最终 URL 或内容完整性时禁止入库。
6. 复用现有知识库上传/更新服务实现 `job_description` 确认入库；重复 URL/hash 提示冲突，不静默复制。
7. 增加 Trace 查询模型，串联模型 Tool Call、Gate 决策、确认、MCP 请求、结果、Artifact 和入库结果。
8. 新增 `tests/unit/test_mcp_tool_adapter.py`、`test_mcp_result_guard.py`、`test_job_description_normalizer.py` 和 `tests/integration/test_job_description_ingestion.py`。

### 依赖关系

依赖 Task8 的确认执行链路、Task5 的 snapshot lease 和现有知识库服务。

### 验收标准

- 超长结果进入模型前被裁剪并带完整裁剪元数据，脱敏内容不泄漏 Token/Cookie/表单值。
- Trace 与 Artifact 保留真实来源 URL、schema hash 和 raw_source_ref。
- 无来源、被裁剪且未恢复完整内容、列表页或登录墙页面不能标记为完整 JD。
- 完整 JD 至少包含标题、公司、地点、职责、必备要求和来源 URL。
- 用户确认前知识库无新 JD；确认后 `job_description` 可检索并返回版本/索引结果。
- `uv run pytest tests/unit/test_mcp_tool_adapter.py tests/unit/test_mcp_result_guard.py tests/unit/test_job_description_normalizer.py tests/integration/test_job_description_ingestion.py -q` 返回 0 failures。

### 预估复杂度

高。外部内容不可信、来源完整性和知识库写入边界需要同时满足。

## Task10：实现 RAG Tool、Skill Registry 与 `job-research` Skill

### 任务目标

把现有知识库检索包装成真实可审计 Tool，建立 Skill 加载机制，并编排岗位搜索、Browser 和简历证据检索。

### 子任务

1. 新增 `src/starter_agent/tools/builtin/knowledge.py`，实现 proposed Tool `retrieve_resume_evidence`：输入 `query` 与 `top_k`，user/project/knowledge base 从 `ToolContext` 注入。
2. Tool 固定检索 `document_type=resume`，复用 `KnowledgeApplicationService.retrieve()` 并返回连续原文 quote、chunk/document/version/section/line/source_ref；不得回退为 `read_resume` 并声称使用 RAG。
3. 新增 `src/starter_agent/skills/models.py`、`parser.py`、`registry.py` 和 `selector.py`，解析带 frontmatter 的 `SKILL.md`，保存不可变 Skill snapshot、依赖和启停覆盖。
4. 候选 Skill 解析成功后原子交换；失败保留旧定义并标记 stale/last_error。
5. 新增 `skills/job-research/SKILL.md`，定义触发与不触发示例、输入、前置条件、工具依赖、步骤、验证、失败处理、输出和 JD 入库确认。
6. Skill 步骤固定为：SerpAPI 搜索 → URL 选择 → Browser 读取 → JD 验证 → RAG 简历证据 → 带来源分析 → 用户确认入库。
7. Skill 只能向 `UnifiedToolExecutor` 提交请求；依赖 Tool/MCP 不可用时返回 `dependency_unavailable` 并按设计降级。
8. 修改 `ContextBuilder`，只向模型注入启用 Skill 的轻量目录；触发后加载完整 Skill 定义，避免全量 Skill 常驻 Context。
9. 新增 `tests/unit/test_resume_evidence_tool.py`、`test_skill_parser.py`、`test_skill_registry.py`、`test_job_research_skill.py` 和 `tests/integration/test_job_research_orchestration.py`。

### 依赖关系

依赖 Task9 的 JD/Trace、Task7 的统一 Executor、现有 RAG 和现有 `search_jobs_serpapi`。

### 验收标准

- `retrieve_resume_evidence` 是 Registry 中真实 Tool，Schema 与实现一致，并只检索当前作用域 resume。
- 无简历证据时返回 no evidence，不生成虚假经历。
- `job-research` 对岗位调研请求触发，对通用建议或纯文本改写不触发外部浏览。
- Skill 依赖不可用时明确指出缺失能力，不隐藏 Tool 输入/输出/失败。
- Skill 发起的每次真实 Tool 请求均经过 Gate。
- `uv run pytest tests/unit/test_resume_evidence_tool.py tests/unit/test_skill_parser.py tests/unit/test_skill_registry.py tests/unit/test_job_research_skill.py tests/integration/test_job_research_orchestration.py -q` 返回 0 failures。

### 预估复杂度

高。需要把已存在的 RAG、动态 Tool 和新 Skill 触发机制组合而不产生旁路。

## Task11：实现能力管理、确认与 Trace 后端 API

### 任务目标

提供真实读取 Manager、Tool Registry、Skill Registry 和 Store 的管理接口，并为所有变更操作加入权限、确认、审计和回滚。

### 子任务

1. 新增 `src/starter_agent/interfaces/capabilities_api.py`，使用 FastAPI `APIRouter` 实现 Server 列表/详情、connect、disconnect、enable、disable、health-check、refresh。
2. 实现 Tool 详情/Schema、启停、审查、allowlist/deny/always-confirm 规则增删和 domain/action/参数范围管理。
3. 实现 Skill 列表/详情、启停、候选 reload、原始定义查看和依赖健康查询。
4. 实现 Pending 查询、确认决定、Tool Trace、Context snapshot 调试接口。
5. 当前本地模式仅允许 loopback 管理请求；抽象 `PrincipalResolver` 与角色 `viewer/operator/admin`。非 loopback 部署在没有标准 OIDC SDK 配置时拒绝管理操作，不自行实现 Token 验证。
6. 所有 mutation 接受 expected revision 或 `If-Match`，返回 operation id、最新 revision 和真实权威状态。
7. connect/disconnect、扩大 allowlist、启用写 Tool、reload 外部定义必须先创建管理确认并显示 diff/风险/影响范围。
8. 操作失败时回滚候选状态，返回稳定错误码并重新读取权威状态；禁止返回静态成功。
9. 在 `src/starter_agent/interfaces/api.py` 挂载 router，并扩展 CORS/headers 仅满足真实接口所需范围。
10. 新增 `tests/unit/test_capability_api_models.py`、`test_management_authorization.py` 和 `tests/integration/test_capabilities_api.py`、`test_confirmation_api.py`。

### 依赖关系

依赖 Task3–Task10 的 Manager、Registry、Gate、确认、Trace 和 Skill 服务。

### 验收标准

- 所有页面需要的数据均来自真实后端组件，不是前端拼造状态。
- viewer 不能变更；operator/admin 权限按策略执行；非 loopback 未配置认证时拒绝管理操作。
- mutation 冲突、失败和超时返回明确错误，旧权威状态仍可查询。
- 单 Server refresh API 不触发其他 Server 变化。
- 确认 API 防重复提交，并在执行前重新运行 Gate。
- `uv run pytest tests/unit/test_capability_api_models.py tests/unit/test_management_authorization.py tests/integration/test_capabilities_api.py tests/integration/test_confirmation_api.py -q` 返回 0 failures。

### 预估复杂度

高。接口数量较多，权限、revision 和失败回滚必须保持一致。

## Task12：实现前端「能力管理」与 MCP/Skills 页签

### 任务目标

在现有 Web 前端新增统一、可操作的能力管理视图，并保证每个控件都对应真实后端接口和失败恢复。

### 子任务

1. 修改 `src/web/index.html`，在“对话/知识库”旁增加“能力管理”一级入口，并扩展 hash 路由：`#/capabilities/mcp-servers` 与 `#/capabilities/skills`。
2. 建立能力管理共同布局：页签、最后刷新时间、全局错误条、loading skeleton、空数据、stale 数据和窄屏单列钻取。
3. 实现 MCP Server 列表与详情：来源、配置/运行时版本、transport、连接/健康/刷新生命周期、能力数量、最近错误和审计时间线。
4. 实现 Tools/Resources/Prompts 子页、Tool Schema 抽屉、启停/审查、allowlist、domain/action/参数范围和 Context 暴露级别。
5. 实现 connect/disconnect/enable/disable/health-check/refresh 控件；操作期间禁用重复操作，展示 operation 阶段，失败后重新 GET 权威状态。
6. 实现 Skills 列表与详情：名称、描述、来源、版本/更新时间、文件位置、启用、依赖、触发/不触发示例、验证、失败策略和原始定义。
7. 实现 Skill enable/disable/reload 操作及管理确认；reload 失败保留旧定义并展示 stale/error。
8. 所有动态文本使用 `textContent`/DOM API，禁止把外部 Schema、错误或 Skill 原文直接拼入 `innerHTML`。
9. 增加键盘、焦点、aria-live、对比度和窄屏测试。
10. 新增 `tests/unit/test_capability_ui_contract.py` 和 `tests/integration/test_capability_ui_api_contract.py`。

### 依赖关系

依赖 Task11 的全部查询和操作 API。

### 验收标准

- `MCP Servers` 与 `Skills` 两个页签可直接访问、刷新后保持后端权威数据。
- 页面区分未配置、未连接、无能力、加载、stale、操作失败和后端不可用。
- 每个管理控件都有真实 API、加载锁、成功后重读和失败回滚。
- 单 Server 刷新只更新目标行与详情，不重置其他 Server。
- 窄屏下无横向关键内容丢失，操作按钮和错误可访问。
- `uv run pytest tests/unit/test_capability_ui_contract.py tests/integration/test_capability_ui_api_contract.py -q` 返回 0 failures。

### 预估复杂度

高。当前前端为单文件，需要控制组件边界并避免继续扩大不可维护的全局状态。

## Task13：实现聊天 Tool 确认卡与可恢复交互

### 任务目标

在对话流中展示后端 Pending 确认，支持三种决定，并准确呈现等待、执行、取消、超时和失败结果。

### 子任务

1. 扩展 `/v1/chat/stream` 事件契约，支持 `confirmation_required`、`confirmation_resolved`、`tool_started`、`tool_completed` 和审计引用。
2. 修改 `src/web/index.html` 的流式事件处理，渲染确认卡：Server、Tool、参数摘要、风险、目标/数据去向、过期时间。
3. 提供「仅本次执行」「执行并加入白名单」「取消」；强制确认动作禁用持久放行并显示原因。
4. 当前 turn 等待确认时禁止重复提交消息或重复决定，但不冻结其他只读页面。
5. 页面刷新后查询 Pending 并恢复卡片；决定请求携带 idempotency key。
6. 确认后展示重新校验、真实执行或拒绝；取消/超时展示未调用 Tool 和审计引用。
7. 处理并发页面决定、Server 刷新、Tool 禁用和 Schema 变化导致的确认失效。
8. 新增 `tests/unit/test_tool_confirmation_ui_contract.py` 和 `tests/integration/test_chat_confirmation_flow.py`。

### 依赖关系

依赖 Task8 的确认服务、Task11 的确认 API 和 Task12 的共享前端状态/错误组件。

### 验收标准

- 确认卡参数、风险和数据去向来自后端 Pending 记录。
- 用户决定前 Server 无真实 Tool 请求。
- 取消、超时和重复点击不产生调用；仅本次调用最多执行一次。
- 加入 allowlist 后只有普通 Tool 后续可自动执行，强制确认仍每次提示。
- 页面刷新能恢复 Pending，最终卡片能链接到审计/Trace。
- `uv run pytest tests/unit/test_tool_confirmation_ui_contract.py tests/integration/test_chat_confirmation_flow.py -q` 返回 0 failures。

### 预估复杂度

中高。重点是 SSE、持久化 Pending 与前端幂等交互的一致性。

## Task14：建立能力目录、运维说明与安全审查记录

### 任务目标

生成可审查的人类可读能力目录和诊断说明，同时确保 Markdown 文档不是运行时授权源。

### 子任务

1. 新增 `docs/capability_catalog.md`，记录内置 Tool、Playwright MCP、RAG Tool 和 `job-research` Skill 的来源、配置/运行时版本、transport、能力、启停、Context 暴露、风险、allowlist、强制确认、外发数据、负责人、健康检查和禁用方式。
2. 对每个 Playwright Tool 记录真实 upstream name、模型别名、schema hash、审查时间和审查结论；不得在未发现真实 Schema 前填写假定义。
3. 新增 `docs/job-research-operations.md`，说明 Node/npx、包缓存、进程、initialize、发现、浏览器依赖、Gate、确认、Tool Result 与刷新诊断。
4. 新增 `docs/job-research-acceptance.md`，定义真实成功记录、MCP 不可用降级记录、证据保存位置和验收判定。
5. 提供 Registry 的脱敏 catalog export 服务或 API；文档由显式审查结果更新，运行时不读取 Markdown 做授权。
6. 新增 `tests/unit/test_capability_catalog.py`，验证文档与当前经审查 Registry 导出的名称/schema hash 不漂移，且不包含 Secret。

### 依赖关系

依赖 Task4 的真实发现、Task7 的策略、Task10 的 Skill/RAG Tool 和 Task11 的导出接口。

### 验收标准

- `docs/capability_catalog.md` 包含需求规定字段，并区分轻量目录与 callable tools。
- Tool 定义来自真实快照，不使用硬编码猜测。
- 运维文档为每个诊断阶段提供可观察信息、稳定错误码和最小重试动作。
- 文档和导出结果不包含 API Key、Token、Cookie、简历全文或个人登录信息。
- `uv run pytest tests/unit/test_capability_catalog.py -q` 返回 0 failures。

### 预估复杂度

中。主要风险是文档与动态 Schema 漂移，需要以真实 Registry 导出为依据。

## Task15：完成权限矩阵、刷新隔离与失败降级自动化测试

### 任务目标

用单元、集成和前端契约测试覆盖全部治理组合，并证明关闭 Tool 后完整 Schema 从真实模型请求中移除。

### 子任务

1. 新增参数化权限矩阵，覆盖 Server/Tool 启停、未审查、deny、always_confirm、allowlist_auto、confirm_once、domain/action/参数范围和数据分类。
2. 覆盖自动执行、仅本次确认、加入白名单、强制确认、取消、超时、重复提交、并发确认和确认后 Schema 变化。
3. 覆盖单 Server refresh 成功/失败、同 Server 并发、不同 Server 隔离、在途调用与旧快照 stale。
4. 覆盖 Server 不可用、Tool 不存在、Schema 无效、页面拒绝、Browser 超时、结果过长、脱敏和来源追溯。
5. 扩展 Provider 请求 Trace/测试适配器，捕获实际发送给 Provider 的 tools payload 和 `context_revision`。
6. 运行一次 Tool 启用请求，记录完整 Schema；关闭 Tool 后发起下一轮请求，断言完整 Name/Description/Input Schema 不存在；重新启用并审查后断言恢复。
7. 增加全量 API/UI 回归，确保失败操作不会在前端留下成功开关。
8. 新增/集中测试文件：
   - `tests/unit/test_tool_governance_matrix.py`
   - `tests/integration/test_mcp_refresh_and_context.py`
   - `tests/integration/test_tool_confirmation_matrix.py`
   - `tests/integration/test_model_request_tool_exposure.py`
   - `tests/integration/test_job_research_degradation.py`
9. 运行相关测试后运行完整回归 `uv run pytest -q`。

### 依赖关系

依赖 Task2 至 Task14 的全部实现。

### 验收标准

- 每一种治理分支至少有一个拒绝/确认/允许断言和“Server 未提前收到调用”断言。
- 真实 Provider 请求 Trace 证明关闭 Tool 后完整 Schema 被移除，不能只检查 UI 或 Registry 内存。
- 刷新失败保留旧快照且其他 Server 测试不受影响。
- 超长结果仍可通过 raw_source_ref 和 URL 追溯。
- 全部新增测试与现有回归返回 0 failures。

### 预估复杂度

高。测试组合多，需避免只验证表面开关而没有观察真实请求和调用边界。

## Task16：执行真实 Playwright MCP 端到端验收与诊断闭环

### 任务目标

在真实环境完成从配置加载到公开 JD、RAG 分析、UI/Trace 和确认入库的完整链路；持续修复发现的问题，直到真实验收通过或出现必须由用户解除的外部阻塞。

### 子任务

1. 新增带 `external` 标记的 `tests/e2e/test_playwright_job_research.py`，测试读取真实运行配置和当时有效的公开 JD URL，不硬编码模型生成页面。
2. 依次检查并记录：
   - Node.js 和 npx 路径/版本。
   - `@playwright/mcp@latest` 下载或缓存。
   - stdio 进程启动、stderr 和退出码。
   - MCP initialize、协议和运行时实际包版本。
   - Tools/Resources/Prompts 真实发现与 Schema hash。
   - 单 Server refresh、其他 Server 隔离和旧快照行为。
   - Tool 审查、启停和下一轮模型 Context 暴露。
   - Gate、非 allowlist 确认卡、一次性 Permit 和 allowlist 自动执行。
   - 浏览器依赖、公开 JD 导航、页面读取和 Tool Result 解析。
   - 结果裁剪、来源 URL、Artifact、UI 和 Trace。
   - RAG 简历引用、带来源匹配分析和用户确认 JD 入库。
3. 使用真实 SerpAPI 搜索结果或用户提供的公开 URL，提取标题、公司、地点、职责、必备要求和最终 URL。
4. 在 UI 执行 Server 刷新、Tool 启停和确认操作，并核对后端 Registry、Provider 请求、MCP 调用和审计一致。
5. 保留一条正常运行记录和一条 MCP 不可用降级记录到 `docs/job-research-acceptance.md`，包含时间、运行时版本、schema hash、Trace/Artifact 引用和非敏感错误。
6. 对每次失败按阶段读取原始 stderr/日志/Trace，定位配置、网络、进程、协议、Schema、权限、确认、Browser 或解析原因；修复后从配置加载重新跑完整链路。
7. 运行命令：

```powershell
uv run pytest tests/e2e/test_playwright_job_research.py -m external -vv
uv run pytest -q
```

8. 只有遇到系统权限、外部网络或授权阻塞时暂停；记录原始错误、排查步骤、阻塞原因和用户最小动作，解除后继续本 Task。

### 依赖关系

依赖 Task1 至 Task15 的全部实现与自动化验证，以及真实 Node.js/npx、网络、SerpAPI 凭据和公开 JD 页面。

### 验收标准

- Starter Agent 真实加载 `config/mcp.json` 并启动 `npx @playwright/mcp@latest`。
- initialize 返回真实 Server 信息，Tools/Resources/Prompts 来自协议发现。
- 单 Server refresh 不影响其他 Server；刷新结果、版本和 Schema 在 UI 可见。
- Tool 关闭后下一轮真实模型请求无完整 Schema，重新启用并审查后恢复。
- 非 allowlist 调用在确认前无 MCP 请求；用户决定后 Gate 重新校验并按策略执行。
- Playwright MCP 真实读取公开 JD，Tool Result、最终 URL、UI 与 Trace 可相互核对。
- RAG 返回可定位简历证据，匹配分析不虚构经历。
- 用户确认前 JD 不入库，确认后文档与索引可查询。
- 正常记录、不可用降级记录、external E2E 和完整回归均满足验收文档。
- 不以 Mock、静态页面、组件测试或模型口述替代任何真实链路步骤。

### 预估复杂度

很高。环境、网络、动态 npm 包、浏览器依赖和真实网站行为都可能产生非确定性，需要完整诊断循环。

## 顺序执行约束

实施必须按 `Task1 → Task2 → Task3 → Task4 → Task5 → Task6 → Task7 → Task8 → Task9 → Task10 → Task11 → Task12 → Task13 → Task14 → Task15 → Task16` 推进。后续 Task 可以消费前序 Task 已通过验收的接口，但不得提前绕过前序安全边界。

只有在用户明确说“确认计划，开始执行”后，才进入代码实施。

