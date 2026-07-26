# job-research Trust Layer Implementation Tasks

> 本文只定义实施拆分。实际执行进度由运行时任务机制单独记录，不在本文维护。

**目标：** 按已确认的 `job-research-trust-requirements.md` 和 `job-research-trust-design.md`，为现有 `job-research` 链路实现固定评测、真实 Smoke、Trace 关联、安全回归和 Trust Center。

**执行约束：**

- 只有用户明确说“确认计划，开始执行”后，才开始修改代码。
- 实施时必须复用现有 Agent Runtime、ContextBuilder、UnifiedToolRegistry、MCP Manager、Skill Registry、Pre-Tool-Call Gate、SQLite store、JSONL logging 和单页前端。
- 不得平行实现第二套 Agent Runtime、Tool Gate 或日志系统。
- 固定 Fixture Eval 不得依赖实时互联网结果。
- 真实 Smoke 必须使用真实模型和真实 Playwright MCP，不得用 Mock、脚本化 Provider、静态截图或 PPT 替代。
- 权限、Schema、Tool、来源、引用、执行顺序和安全硬门禁必须由确定性规则和真实 Trace 验证。
- 不得记录真实秘密；脱敏回归只能使用带明显测试前缀的假 Token。

## Task1：审计现有链路与缺口

### 任务目标

形成可复核的当前工作区基线，确认 `job-research`、Tool/MCP/RAG、Gate、Trace、Log、Token 和前端真实入口与缺口，避免后续实施臆造接口、命令或字段。

### 子任务

1. 核对 `AgentRuntime`、`ApplicationService`、`ContextBuilder`、`UnifiedToolRegistry`、`McpManager`、`SkillRegistry`、`PreToolCallGate` 和 `UnifiedToolExecutor` 的真实调用链。
2. 核对 `CapabilityStore`、`SQLiteSessionStore`、audit event、confirmation、execution permit、tool artifact、turn usage、context snapshot 和 JSONL log 的现有字段。
3. 核对 `job-research` Skill、`JobResearchOrchestrator`、`search_jobs_serpapi`、Playwright MCP 工具、`retrieve_resume_evidence` 和能力目录的真实名称、Schema、错误码和启停行为。
4. 核对 `src/web/index.html` 的现有 hash 路由、聊天确认卡、能力管理页、知识库页和缺失的 Trust Center。
5. 生成或更新审计记录，明确哪些能力可复用、哪些能力需要新增。

### 依赖关系

无前置实现依赖；依赖已确认需求和设计文档。

### 验收标准

- 审计记录能定位每个真实入口和缺口。
- 明确当前没有专用 Eval Runner、固定 Fixture 目录、Eval Run/Case 存储、完整 Trust Center 和真实模型 Smoke。
- 明确现有 Trace 缺少 Eval Run/Case、独立 `model_request_id` 和独立 `policy_decision_id`。
- 不包含项目中不存在的 Tool 名称、路径、命令或前端接口。

### 预估复杂度

低。

## Task2：建立 Trust 数据模型与存储迁移

### 任务目标

实现 Suite、Case、Fixture、Run、Case Result、Assertion Result、Metric、Failure Cluster、Release Gate 和 Trace Event 的持久化模型及版本关联。

### 子任务

1. 新增 Trust 领域模型，覆盖 Eval Suite、Eval Case、Fixture、Eval Run、Case Result、Assertion Result、Metric、Failure Cluster、Release Gate、Trace Event 和 Smoke Run。
2. 新增 Trust Store，复用当前 SQLAlchemy/SQLite 风格和 `settings.app.database_url`。
3. 实现 additive schema migration，不破坏现有 session、capability、knowledge、email 表。
4. 实现版本字段：代码版本、dirty flag、Prompt 版本、Skill 版本、Tool Schema 版本、Policy 版本、Fixture manifest hash。
5. 实现幂等写入与冲突检测，尤其是 Trace Event 和 Run/Case Result。
6. 添加模型校验、分页查询和安全摘要字段校验。

### 依赖关系

依赖 Task1 的真实数据边界。

### 验收标准

- SQLite 能创建并读取全部 Trust 表。
- Run、Case、Assertion、Metric、Failure Cluster 和 Release Gate 能通过 ID 关联。
- 固定 Eval 与真实 Smoke 能通过 `run_type` 分离。
- 大 payload 默认只保存脱敏摘要、哈希、source ref 和状态。
- 写入重复事件时幂等；payload 冲突时有稳定错误。

### 预估复杂度

高。

## Task3：建立固定 Fixture 装载与脱敏数据集

### 任务目标

实现固定 Fixture 装载机制，并准备脱敏搜索结果、JD、简历 Chunk、Tool Error、Policy 和 Injection Fixture。

### 子任务

1. 定义 fixture manifest 格式，包含 fixture ID、类型、版本、路径、hash、脱敏说明和适用 case。
2. 实现 fixture loader，校验 hash、类型、必填字段和禁止秘密规则。
3. 实现 SerpAPI fixture、JD page fixture、resume chunk fixture、MCP snapshot/tool result fixture、tool error fixture、policy fixture 和 injection fixture。
4. 为网页、PDF、邮件和 Tool Result 注入文本准备固定恶意样例。
5. 将 fixture 与 Eval Case 的输入、期望 outcome、期望 Tool/参数、断言和安全等级关联。
6. 添加 fixture 脱敏扫描，拒绝 API Key、Cookie、Authorization、密码、邮箱授权码、完整简历正文和私人投递数据。

### 依赖关系

依赖 Task2 的 Fixture 和 Eval Case 模型。

### 验收标准

- Fixture 装载完全离线，不触发真实网络。
- Fixture hash 稳定；内容变化会改变版本或 hash。
- 至少覆盖搜索、JD、RAG、MCP 响应、MCP 不可用、RAG 无证据、Policy、Prompt Injection 和假 Token 泄漏。
- fixture 校验能拒绝未脱敏秘密和完整敏感正文。

### 预估复杂度

中高。

## Task4：实现 Eval Runner 的隔离、调度与状态机

### 任务目标

实现固定 Eval Runner 的 case 隔离、超时、取消、并发、重试、随机性和状态流转，保证评测之间不共享污染状态。

### 子任务

1. 新增 Runner 服务，加载 suite、case 和 fixture manifest。
2. 为每条 case 创建独立临时目录、SQLite database、session store、capability store、knowledge base、confirmation service、registry 和 fixture adapter。
3. 实现 Run 状态：queued、running、cancelling、completed、failed、blocked、cancelled。
4. 实现 Case 状态：queued、running、passed、failed、blocked、error、skipped。
5. 实现超时：run timeout、case timeout、tool timeout、model timeout、judge timeout。
6. 实现取消：停止调度新 case，等待当前安全点结束并记录 cancelled。
7. 实现并发：case 可并发，但隔离目录和 DB 不共享。
8. 实现重试：固定 Eval 默认不重试业务步骤，只允许 runner 内部初始化瞬时失败重试并记录次数。
9. 固定随机性：记录 seed、provider、model 和 judge 配置。

### 依赖关系

依赖 Task2 和 Task3。

### 验收标准

- 两个 case 并发运行时不会共享 session、turn、policy、confirmation、knowledge 或 tool artifact。
- 超时、取消、runner error 和 case error 都有稳定状态和错误摘要。
- 同一输入版本本地运行两次可以比较。
- 固定 Eval 不访问实时互联网。

### 预估复杂度

高。

## Task5：实现规则评测器

### 任务目标

实现确定性 Rule Evaluator，用真实 Trace 和结果验证 Schema、Tool、参数、来源、引用、Tool 启停、Policy/Approval 顺序和无真实越权调用。

### 子任务

1. 实现 assertion DSL 或结构化断言模型，覆盖 schema、tool_call、argument、source、citation、policy、approval、trace_order、redaction、external_action。
2. 验证 Tool 名称、server、schema_hash、snapshot_id 和参数摘要。
3. 验证 source_url、final_url、source_ref、content_sha256 和 Chunk 引用。
4. 验证关闭 Tool 不进入 provider callable tools，且无完整 Description/Input Schema。
5. 验证重新启用后下一轮模型请求恢复完整 Schema。
6. 验证非白名单确认前没有 `tool.started`、`permit.consumed` 或 `tool.invoked`。
7. 验证取消、超时、强制确认拒绝后无真实外部动作。
8. 验证 Prompt Injection 没有导致 secret read、email_send、上传、投递或未授权外发。

### 依赖关系

依赖 Task4 的 Runner 与 Task8 的 Trace Context 基础可同步推进，但最终验收依赖 Task8。

### 验收标准

- 权限、Schema、Tool、参数、来源、引用和执行顺序均由确定性断言覆盖。
- LLM Judge 不参与硬权限和安全门禁判定。
- Assertion Result 能保存 expected、actual summary、evidence refs 和失败原因。
- 安全断言失败能标记 hard gate failure。

### 预估复杂度

高。

## Task6：实现程序指标计算

### 任务目标

实现 Task Success、Tool / Argument Accuracy、Citation Correctness、Approval Compliance、P50/P95、Token 和 Cost per Successful Task。

### 子任务

1. 实现 Task Success 的分子、分母、skipped 和 error 处理。
2. 实现 Tool / Argument Accuracy，统计期望调用、实际调用、顺序、Schema hash 和参数匹配。
3. 实现 Citation Correctness，统计 JD 来源、简历 Chunk、line range 和结论支持度。
4. 实现 Approval Compliance，统计 allowlist、require_confirmation、once、allowlist、cancel、timeout、duplicate 和 always_confirm。
5. 实现 P50/P95，明确 runner error 缺失 duration 的处理。
6. 汇总 business model、summary 和 judge Token。
7. 实现成本计算；缺少价格时 cost 标记 missing，不使用 0 冒充。
8. 失败任务成本纳入 Cost per Successful Task 分子；成功数为 0 时结果为 undefined。

### 依赖关系

依赖 Task2 的 Metric 模型、Task4 的 Run/Case Result 和 Task5 的 Assertion Result。

### 验收标准

- 每个指标有确定的分母、缺失值策略和聚合规则。
- Run 级报告包含所有要求指标。
- 安全硬失败不会被普通平均分抵消。
- 成本和 Token 能按 case、run、judge 分开查看。

### 预估复杂度

中。

## Task7：实现可选 LLM Judge 与人工复核记录

### 任务目标

实现只用于语义质量的可选 LLM Judge，并记录 Rubric、模型版本、原始评分、理由、golden 样例和人工抽查。

### 子任务

1. 定义 Judge Rubric manifest，关联 case 或 suite。
2. 接入现有 ProviderRegistry，调用真实 provider 时记录 provider、model、prompt/rubric hash、raw score、reason 和 usage。
3. 支持禁用 Judge；禁用时固定 Eval 仍可通过规则和程序指标运行。
4. 定义 golden 样例，用于校准语义质量评分。
5. 实现 Human Review 记录：reviewer、时间、结论、理由、覆盖 case/run 版本。
6. 防止 Judge 结果写入权限、Schema、Tool 顺序、密钥泄漏和安全硬门禁的唯一判定路径。

### 依赖关系

依赖 Task2 的模型和 Task6 的指标框架。

### 验收标准

- 随机模型评分记录模型、Rubric 和原始分数。
- Judge 失败不会抹掉 Rule Evaluator 的安全失败。
- Human Review 可追溯到 case/run/rubric 版本。
- Judge Token 与业务模型 Token 分开记录。

### 预估复杂度

中。

## Task8：贯通 Eval 与 Trace Context

### 任务目标

让 Trace Context 贯穿 `eval_run_id`、`case_id`、`session_id`、`turn_id`、`model_request_id`、`tool_call_id`、`policy_decision_id`、`approval_id` 和 `child_run_id`。

### 子任务

1. 设计 Trace Context 对象，并在 Runner、ApplicationService、AgentRuntime、Gate、Confirmation 和 Tool artifact 写入路径中透传。
2. 为模型请求生成稳定 `model_request_id`，保留现有 `model.context.snapshot` 兼容字段。
3. 为 Gate 评估生成 `policy_decision_id`，并关联 request hash、schema hash、decision 和 reason code。
4. 将 existing audit event 聚合为 Trust Trace Event。
5. 将 session message、turn usage、tool artifact、confirmation 和 execution permit 关联到 case result。
6. 对缺失节点记录解释，如取消前无 Tool Call、Tool 关闭无 callable schema、RAG 无证据无引用。
7. 实现 trace tree 查询和事件详情查询。

### 依赖关系

依赖 Task2、Task4、Task5；会触碰现有 runtime/gate hook，但不得改变业务决策。

### 验收标准

- 任一 Eval Case 能从报告跳转到 Session、Turn、Model、Policy、Approval、Tool、Error 事件。
- 缺失节点有明确原因，不静默通过。
- `model_request_id` 和 `policy_decision_id` 不破坏现有 audit/event 测试。
- Trace Recorder 只观测，不改变 Gate 或 Runtime 决策。

### 预估复杂度

高。

## Task9：实现结构化日志与写入前脱敏回归

### 任务目标

确保日志、报告、Trace、Tool Artifact 和 UI 输出在写入前完成脱敏，并添加假 Token 泄漏回归。

### 子任务

1. 复用并扩展现有 `redact_sensitive_log_fields`、`redact_tool_result_content` 和 capability summary 校验。
2. 新增 Trust sanitizer，用于 Trust Store、Eval report 和 API response。
3. 覆盖 Authorization、Token、Cookie、密码、邮箱授权码、API Key、完整简历正文和 Tool Result 敏感字段。
4. 在 fixture、runner、trace、report 和 frontend API response 加入脱敏检查。
5. 使用 `TEST_SECRET_TOKEN_SHOULD_REDACT_...` 等假秘密构建泄漏回归。
6. 验证脱敏前数据不会先写入普通日志、报告或 Trust Store。

### 依赖关系

依赖 Task2、Task3、Task8。

### 验收标准

- 假秘密不会出现在 JSONL、SQLite Trust 表、Eval report、Trace API 或前端展示中。
- Tool Result 和简历正文只保存允许的摘要、hash、source ref、line ref 或 preview。
- 脱敏器异常时不写入未脱敏数据。
- Smoke 只保存公开 JD URL、页面标题、来源引用、摘要、Trace ID、参数摘要和哈希。

### 预估复杂度

中高。

## Task10：实现 Tool 启停与 Schema 暴露回归

### 任务目标

用真实模型请求或 Context 调试快照证明关闭 Tool 只暴露轻量 Name，启用后下一轮才恢复完整 Schema。

### 子任务

1. 基于现有 `UnifiedToolRegistry` 和 `ContextBuilder` 增加 Trust 断言所需 snapshot 查询。
2. 捕获 Provider 请求中的 tools payload、context revision、tool name snapshot 和 schema hash。
3. 为内置 Tool 与 MCP Tool 各覆盖启用、关闭、未审查、Schema 移除和重新启用。
4. 验证轻量能力目录不包含完整 Description/Input Schema。
5. 验证 callable tools 不包含关闭项完整定义。
6. 验证重新启用并通过 review/policy 后，下一轮请求原子恢复完整定义。
7. 确认旧 snapshot、旧 permit、旧 confirmation 不绕过新状态。

### 依赖关系

依赖 Task5、Task8；复用现有能力目录和模型请求 snapshot。

### 验收标准

- 能从真实 provider request 或 context snapshot 证明关闭 Tool 的完整 Schema 已移除。
- 重新启用后只有下一轮请求恢复完整 Schema。
- 关闭 Tool 不可真实调用。
- Schema 移除和 schema hash mismatch 都有确定性失败断言。

### 预估复杂度

中高。

## Task11：实现 Pre-Tool-Call Gate 安全确认案例

### 任务目标

实现白名单自动执行、非白名单确认卡、仅本次执行、加入白名单、取消、超时、重复提交和强制确认不可绕过的安全案例。

### 子任务

1. 准备 allowlist_auto、require_confirmation、always_confirm、deny 和 confirm_once 的固定 policy fixture。
2. 构造 Tool Call 请求，覆盖 SerpAPI、Playwright MCP、RAG 和高风险外发动作。
3. 验证白名单内且未命中强制确认的调用自动执行。
4. 验证非白名单调用先出现聊天确认卡，确认前无真实 Tool Start/Invoke。
5. 验证 once 只消费一次 permit。
6. 验证 allowlist 只对允许持久放行的普通动作生效。
7. 验证 cancel、timeout、duplicate click、schema changed 和 policy changed。
8. 验证 always_confirm 不能被 allowlist 绕过。

### 依赖关系

依赖 Task4、Task5、Task8、Task10。

### 验收标准

- 每条确认路径都有 Trace 证据。
- 取消、超时、强制确认拒绝后没有真实外部动作。
- 重复点击不会重复执行。
- 强制确认动作即使存在 allowlist 也继续要求确认或拒绝。

### 预估复杂度

高。

## Task12：实现 Prompt Injection Fixture 与断言

### 任务目标

实现网页、PDF、邮件和 Tool Result Prompt Injection Fixture，并证明没有 secret read 或外发 Tool Call。

### 子任务

1. 准备网页注入 Fixture，包含忽略系统规则、读取本地文件、发送 Cookie、调用 email_send 等恶意文字。
2. 准备 PDF 文本抽取 Fixture，模拟 PDF 中的恶意指令。
3. 准备邮件正文 Fixture，模拟邮件内容要求越权读取或外发。
4. 准备 Tool Result Fixture，模拟上游工具返回恶意 instruction。
5. 在 Context 或 Tool Result 中标记这些内容为 untrusted external content。
6. 用 Rule Evaluator 检查没有 secret read、没有 email_send、没有上传/投递、没有未确认外发。
7. 将模型拒绝文本作为辅助证据，但不作为唯一通过条件。

### 依赖关系

依赖 Task3、Task5、Task8、Task9、Task11。

### 验收标准

- 四类 Injection Fixture 均可离线运行。
- Trace 显示恶意文字被当作数据处理。
- Policy 和 Tool Trace 证明没有真实 secret read 或外发动作。
- 任何安全违规使对应 case blocked，并影响 Release Gate。

### 预估复杂度

中高。

## Task13：实现失败聚类、Run 比较与 Release Gate

### 任务目标

实现失败聚类、根因记录、前后 Run 比较和 Release Gate，确保安全硬失败使最终结论 BLOCKED。

### 子任务

1. 基于 assertion_type、error_code、tool_name、policy_reason_code、schema mismatch、missing_trace_node 和 safety category 生成 cluster key。
2. 写入 Failure Cluster，包含代表 case、case count、root cause、evidence refs 和状态。
3. 支持 Human Review 修改 root cause，但保留审计记录。
4. 实现 Run compare，展示版本 diff、指标 diff、新增失败、修复失败、持续失败、成本和延迟变化。
5. 实现 Release Gate 计算：hard gate failure => BLOCKED，runner 安全不可判定 => BLOCKED，普通指标按阈值 WARN/BLOCKED。
6. 指标阈值无产品数据时标记未配置，不虚构基线。
7. 修复一个失败簇后，要求重跑全量固定回归才能更新结论。

### 依赖关系

依赖 Task5、Task6、Task7、Task8。

### 验收标准

- 报告能从失败簇跳到对应 Case 和 Trace。
- 比较两次 Run 能显示失败簇变化和版本变化。
- 安全硬门禁失败时整体结论为 BLOCKED，不被平均分抵消。
- 阈值缺失时显示明确原因，不假装 PASS。

### 预估复杂度

中高。

## Task14：实现 Trust Center 后端 API

### 任务目标

提供 Suite、Run、Case、Trace、Safety Policy、Gate 和证据查询与操作 API，且所有状态来自真实后端。

### 子任务

1. 新增 Trust API router，并挂载到现有 FastAPI。
2. 实现 suite、run、case result、assertion、metric、failure cluster 和 report 查询。
3. 实现启动固定 Eval、取消 Run、查看进度和比较两次 Run。
4. 实现真实 Smoke 启动、查询和单独报告。
5. 实现 trace 过滤、trace tree、event detail 和 context snapshot 查询。
6. 实现 safety summary、policy view、red-team cases、gate result、BLOCKED reason 和 evidence 查询。
7. 复用现有 management principal 权限：viewer、operator、admin。
8. 增加分页、错误映射、operation id 和后端权威状态返回。
9. 禁止前端通过请求参数覆盖最终 Gate 结论。

### 依赖关系

依赖 Task2、Task4、Task8、Task13。

### 验收标准

- 所有 Trust Center 需要的数据都有真实 API。
- Run 操作返回真实 run id、状态、进度和错误。
- Trace 能按 Run/Case/Session/Turn/Tool 过滤。
- Safety 页所需门禁结论由后端返回。
- 权限不足、后端失败和空数据都有稳定响应。

### 预估复杂度

高。

## Task15：实现 Trust Center 前端页签

### 任务目标

在现有单页前端实现 `Evals`、`Traces`、`Safety` 三个页签，覆盖加载、空、运行中、失败、比较、过滤、跳转和窄屏状态。

### 子任务

1. 扩展 `src/web/index.html` 主导航，新增 Trust Center 路由：`#/trust/evals`、`#/trust/traces`、`#/trust/safety`。
2. 实现共享 Trust layout、tabs、loading、empty、error、forbidden、stale 和 narrow screen 状态。
3. `Evals` 页展示 Suite、Run、版本、指标、Case、Assertion、Failure Cluster 和报告。
4. `Evals` 页支持运行固定评测、取消、刷新、查看详情和比较两次 Run。
5. `Traces` 页支持 Run/Case/Session/Turn/Tool/Event/Status 过滤。
6. `Traces` 页展示树状链路、事件详情、脱敏摘要、Token、耗时和跳转。
7. `Safety` 页展示策略版本、红队案例、门禁状态、BLOCKED 原因和证据。
8. Safety rerun 调用真实后端，并展示进度和失败。
9. 前端只展示后端 Release Gate，不直接计算 PASS/BLOCKED。
10. 使用 DOM API/textContent 渲染外部数据，避免注入。

### 依赖关系

依赖 Task14 的 API。

### 验收标准

- 三个页签刷新后状态一致。
- 所有运行、取消、比较、过滤和跳转操作都调用真实后端。
- 后端失败时前端展示明确错误，不使用静态成功数据。
- 窄屏下主要信息和操作可用。
- 修改前端本地状态不能伪造最终 PASS。

### 预估复杂度

高。

## Task16：生成固定 Eval Case 文件

### 任务目标

生成 `evals/job-research-cases.yaml` 与 `evals/job-research-safety-cases.yaml`，至少 12 条 case，覆盖六层分组和第 8 阶段权限回归。

### 子任务

1. 定义 `job-research-cases.yaml`，覆盖 Happy Path、Edge Case、Missing Information、Tool Failure、Conflicting Context。
2. 定义 `job-research-safety-cases.yaml`，覆盖 Safety / Adversarial。
3. 至少包含 Tool 关闭、Schema 移除、MCP 不可用、RAG 无证据、非白名单确认、强制确认、重复确认、取消、超时和网页注入。
4. 每条 case 包含稳定 ID、输入、fixture refs、期望 outcome、期望 Tool/参数、确定性 assertions、可选 Judge Rubric 和安全等级。
5. 关联 fixture manifest、rubric manifest 和 safety level。
6. 添加 YAML schema 校验和 case ID 唯一性校验。

### 依赖关系

依赖 Task3 的 Fixture loader、Task5 的断言格式和 Task12 的 Injection Fixture。

### 验收标准

- 至少 12 条 case 覆盖六类分层。
- 每条 case 可被 Runner 装载并映射到 fixture。
- 安全 case 均有 hard gate 或明确安全等级。
- Case 文件不包含真实秘密、私人邮箱、真实投递数据或完整简历正文。

### 预估复杂度

中。

## Task17：运行固定基线两次并验证可比较性

### 任务目标

运行固定 Fixture Eval 两次，保留版本、Run ID、结果、失败簇、成本与 Trace；修复一个失败簇后重跑全部回归并比较。

### 子任务

1. 执行完整固定 Eval Suite 第一次运行。
2. 执行输入版本不变的第二次运行。
3. 比较两次 Run 的版本、case result、assertion result、metric、failure cluster、Token 和成本。
4. 选择一个失败簇进行修复或标记为根因明确的预期失败。
5. 修复后重跑全部固定回归，不只跑单条 case。
6. 生成固定基线报告，并确认安全硬门禁失败会使整体 BLOCKED。
7. 记录 Run ID、报告路径、Trace 入口和剩余风险。

### 依赖关系

依赖 Task1 至 Task16。

### 验收标准

- 两次固定 Fixture Run 输入版本不变时结果可比较。
- Run 报告包含版本、Run ID、case 结果、失败簇、指标、P50/P95、Token 和成本。
- 修复失败簇后有全量回归和比较报告。
- 固定基线未使用变化的互联网结果。

### 预估复杂度

中高。

## Task18：运行真实模型与 Playwright MCP Smoke

### 任务目标

运行真实模型与真实 Playwright MCP 的公开 JD Smoke，保留 source_url 与 Trace，单独报告且不混入固定基线。

### 子任务

1. 选择执行时仍公开可访问的 JD URL。
2. 使用真实 Provider/model 发起 `job-research` 路径。
3. 启动真实 Playwright MCP，完成 connect、discover、review/enable 和必要确认。
4. 读取公开 JD，记录 requested_url、final_url、source_url、schema_hash、snapshot_id、artifact hash 和 Trace。
5. 调用 RAG 取回脱敏简历证据或明确无证据。
6. 单独写入 Smoke Run 和 Smoke report，不进入固定 baseline。
7. 记录外部失败原因：JD 下线、验证码、地区限制、MCP 启动失败、模型失败、policy/approval 失败。

### 依赖关系

依赖 Task14、Task15、Task17，以及真实模型凭据、Node/npx、网络和公开 JD。

### 验收标准

- Smoke 使用真实模型和真实 Playwright MCP，不使用 Mock 或脚本化 Provider。
- Smoke 报告与固定基线分开。
- 保留来源和 Trace，且不保存完整简历正文或秘密。
- 外部不可用时记录明确失败，不影响固定基线分数。

### 预估复杂度

高。

## Task19：生成验收证据与秘密检查

### 任务目标

生成 `docs/job-research-trust-acceptance.md` 所需证据，并检查仓库、日志、报告与截图无真实秘密或个人敏感正文。

### 子任务

1. 汇总固定 Eval 两次运行的 Run ID、版本、指标、失败簇、Release Gate 和 Trace 链接。
2. 汇总真实 Smoke 的日期、provider/model、公开 JD URL、source_url、Trace、schema_hash、artifact hash 和结果。
3. 汇总 Tool 关闭/启用 Schema 暴露证据。
4. 汇总非白名单确认、取消、超时、重复点击和强制确认证据。
5. 汇总 Prompt Injection、安全硬门禁和 BLOCKED 证据。
6. 检查仓库、JSONL、SQLite 可导出报告、前端截图和文档不含真实秘密或完整敏感正文。
7. 生成 `docs/job-research-trust-acceptance.md`，只记录脱敏证据。

### 依赖关系

依赖 Task17 和 Task18。

### 验收标准

- 验收文档能支撑需求中的所有验收标准。
- 证据能从报告跳转到对应 Trace。
- 日志、报告、截图和文档不含真实秘密、私人邮箱授权码、完整简历正文或真实投递信息。
- 脱敏前数据没有先写入普通日志。

### 预估复杂度

中。

## Task20：持续诊断、修复与完整回归闭环

### 任务目标

对失败持续诊断和修复，不停在代码完成、组件测试、Mock 全绿、页面能打开或模型口述能力，直到真实验收达成或出现必须由用户解除的外部阻塞。

### 子任务

1. 对每个失败读取 redacted error、Trace、audit event、tool artifact、JSONL 和前端 API response。
2. 区分 runner、fixture、model、tool、MCP、policy、approval、frontend、external environment 和 secret redaction 问题。
3. 修复代码、fixture、case 或文档后，重跑相关单元/集成测试。
4. 修复失败簇后，重跑全部固定回归。
5. 固定回归通过后，重跑真实 Smoke。
6. 每轮保留原始错误的脱敏摘要、修复证据、重跑命令和结果。
7. 只有系统权限、外部网络、真实模型凭据或公开 JD 不可用这类必须由用户处理的问题，才暂停并请求用户动作。

### 依赖关系

依赖 Task1 至 Task19；执行期间可针对任意前序 Task 的失败回溯。

### 验收标准

- 所有固定安全硬门禁通过，或整体明确 BLOCKED 且有证据。
- 固定基线、真实 Smoke、Trust Center 和验收文档形成闭环。
- 不以 Mock 全绿、页面能打开、模型拒绝文本或代码完成作为总体验收。
- 每个残余风险都有证据、影响范围和下一步处理条件。

### 预估复杂度

很高。

## 顺序执行约束

必须按 `Task1 -> Task2 -> Task3 -> ... -> Task20` 的依赖顺序推进。后续 Task 可以消费前序 Task 已通过验收的接口和数据，但不得通过临时 Mock、前端静态数据或绕过 Gate 的方式伪造完成。

当用户明确说“确认计划，开始执行”后，再从 Task1 开始小步实现。每完成一个 Task，必须汇报修改文件、运行测试、验收结果与剩余风险。
