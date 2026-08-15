# 求职 Agent 执行编排实施任务计划

## 1. 文档目的

本文将已确认的 `job-application-orchestration-requirements.md`、`job-application-orchestration-design.md` 与 `agent-runtime-framework-decision.md` 转换为可顺序执行、可独立验收的工程任务。当前文档只定义实施计划，不授权修改代码、启用新路径或发布功能。

实施遵循以下约束：

1. 保留唯一的 `AgentRuntime`、`RunContext`、`SQLiteRunStore`、Plan/Todo、Budget Ledger、Delegation、Gate、Eval 与 Trace；所有新增能力以 Schema、策略组件、Facade 或 Adapter 接入。
2. Direct、Workflow、Tool Loop、Plan/Delegation、Human Review 是条件分支，不是每次请求都经过的固定流水线。
3. 高风险 Tool 和不可逆动作必须继续经过现有 `PreToolCallGate`、Confirmation/Email Approval 与 Unified Tool Executor；Router、Planner、Judge 均不得绕过 Gate。
4. 当前框架结论为“不迁移”。本计划不创建 LangChain、LangGraph 或 OpenAI Agents SDK 迁移任务，也不创建 Checkpoint、跨重启节点恢复或 LangGraph Interrupt 实现任务。
5. 后台任务只保证已有任务级持久化、事件驱动续跑和明确的 `interrupted/failed` 终态；不承诺步骤级 Checkpoint。
6. 每个任务先补契约/Fixture 和失败测试，再做最小实现，最后运行该任务相关回归并保存命令、结果与 Trace/Artifact 证据。
7. Task1 至 Task21 按依赖顺序执行；只有在依赖均完成且写集合互不冲突时，才允许并行处理同一 Task 内的独立子任务。

## Task1：完成实现前审计并冻结复用与框架边界

### 任务目标

以真实仓库为准核对已确认设计，形成实现级组件映射、契约基线与框架选型证据，防止后续新增第二套 Runtime、State Store、Plan、Context、Gate、Budget、Delegation、Eval 或 Trace。

### 子任务

1. 审计现有 Runtime/Tool Loop、Workflow、Plan/Todo、Context Summary/Trim/Memory、Budget、Delegation、Gate/Approval、Eval/Safety Gate、Trace、状态持久化、API/SSE 和运行详情前端。
2. 记录每项能力的权威实现、调用入口、持久化来源、测试契约、可扩展点与禁止复制边界。
3. 将 Router、Planner、Plan Validator、Task Manager、Executor、Verifier、Recovery、Budget Manager、Model Router 分别映射到现有组件或最小新增适配层。
4. 核对 `docs/agent-runtime-framework-decision.md` 中“不迁移”的证据仍与代码一致；记录保留模块、适配层、功能开关和回滚入口。
5. 生成实现审计记录，列出受影响文件、数据库迁移策略、兼容性风险和基线测试命令。
6. 明确 Checkpoint/Interrupt 只保留在选型说明中的用途、采用信号及其与 Summary/Memory 的区别，不进入实现 Backlog 或验收门禁。

### 依赖关系

- 无；为全部后续任务的前置任务。

### 验收标准

- 审计记录覆盖需求指定的全部现有模块，并为每个新增组件给出唯一复用落点。
- 能证明 `AgentRuntime`、`RunContext`、`SQLiteRunStore`、Approval Gate、Budget Ledger、Delegation 与 Trace 仍是各自唯一权威实现。
- 框架结论与已确认 ADR 一致；因结论为“不迁移”，明确跳过框架迁移/Spike 实施任务，并保留未来采用信号。
- 基线单元、集成和关键 E2E 测试结果被保存；既有失败与环境阻塞被单列，不伪装为通过。
- 审计未产生产品代码变更，也未引入新依赖。

### 预估复杂度

- 中（1–2 人日）。

## Task2：定义统一执行 State Schema 与显式状态图契约

### 任务目标

建立版本化 `ExecutionState`/`RunContext` 编排 Schema，以及 Node、条件 Edge、终止原因和持久化投影契约，为所有路径提供单一状态转移语义。

### 子任务

1. 定义包含 `route`、`plan`、`current_step`、`outputs/artifact_refs`、`budget`、`pending_action`、`revision_count`、`background_task`、`child_runs` 和关联 ID 的执行 State Schema。
2. 定义 Route Decision、Plan、Plan Step、Background Task、Parent Run、Child Run、Task Event、Join Decision、Validation Result、Verify Result、Recovery Attempt、Budget Snapshot、Model Decision 与 Pending Action 的版本字段、枚举和值约束。
3. 将现有 Router、Planner、Task Manager、Executor、Verifier、Recovery 映射为可选择 Node；将 Gate、Budget、Plan Validation、Join Policy、风险与并行资格映射为条件 Edge。
4. 定义合法状态转移、CAS/version 更新、终止条件、停止原因和非法转移错误；避免把所有 Node 串成固定必经链路。
5. 定义活动 State 在 `RunContext` 中的所有权，以及后台/等待快照在现有 Parent payload/Run Store 中的投影和兼容读取规则。
6. 为 Direct 不创建 Plan、不创建 Child，以及 Human Review 不执行 Tool 编写结构级约束测试。

### 依赖关系

- 依赖 Task1 的组件映射、权威数据源和兼容性基线。

### 验收标准

- Schema 可序列化、版本化并通过往返测试；未知可选字段保持向后兼容，非法枚举和非法转移被确定性拒绝。
- 状态图至少表达 Direct、Workflow、Tool Loop、Plan/Delegation、Human Review、Merge、Verify、Recovery、Stop 与 End 的条件分支。
- `RunContext` 与 `SQLiteRunStore` 的职责清晰，没有新增第二状态数据库或由 SSE/前端推导权威状态。
- Direct Fixture 证明 Planner、Task Manager 和 Multi-Agent 均未被调用。
- 高风险 Fixture 证明在批准前不会进入 Tool 执行节点。

### 预估复杂度

- 高（3–4 人日）。

## Task3：扩展统一 Budget Manager 与运行时预算条件

### 任务目标

在现有 Budget Ledger 上增加编排维度和统一 Facade，使 steps、tokens、cost、wall-clock、tool_calls 及既有 model_calls 能在 Parent/Child 范围预检、预留、记账和停止。

### 子任务

1. 扩展预算限制、分配、消费、释放与 Snapshot Schema，增加 `steps`，保留既有保护维度和单位定义。
2. 实现 Node/Step/Tool/模型调用前预检，以及 Node/Step/Child 结束后的幂等记账。
3. 实现 fan-out 前的 Parent 总预算与每 Child 预算预留；Child 完成、失败、超时或取消后结算和归还未用额度。
4. 定义 soft/hard limit、预算不足时的 `partial/stop/human_review` 条件 Edge 和稳定停止原因。
5. 超限结果返回已完成项、未完成项、已用/剩余预算与可恢复方式，不继续 Reflection、Planner 或 Tool 调用。
6. 补充并发记账、重复事件、重试不重复收费和 cost 缺失时保守估算测试。

### 依赖关系

- 依赖 Task2 的 Budget Snapshot、State 与 Edge 契约。

### 验收标准

- 五个需求维度均可配置并成为真实状态转移条件；现有 model_calls 限制保持兼容。
- fan-out 无法完整预留时不会超发 Child；允许部分启动时必须由策略显式声明。
- 重放同一消费事件不产生重复扣账，Parent 消费等于 Child 结算与 Parent 自身消费的可解释汇总。
- 预算耗尽 Fixture 在边界处停止，输出 completed/incomplete/recovery 信息，后续无额外模型或 Tool 调用。
- 未创建第二预算账本。

### 预估复杂度

- 高（3–4 人日）。

## Task4：实现可配置 Model Router 与决策记录

### 任务目标

根据任务复杂度、所需能力、成本、延迟和风险策略选择已配置且可用的模型，并以结构化 Model Decision 记录选择和 fallback，不硬编码秘密或不存在的模型。

### 子任务

1. 从现有 Provider/Model Registry 读取能力、可用性、价格/配额、延迟等级与配置标识。
2. 定义 Model Router 输入、规则优先级、候选过滤、选择理由、fallback chain 与不可用错误。
3. 将模型选择用于 Router/Planner/Executor/可选 Judge 等实际需要模型的节点；确定性节点不得触发模型选择。
4. 高风险任务仍依赖确定性验证、Gate 和人工确认，不以盲目升级大模型替代安全控制。
5. 将 `model_decision_id`、候选摘要、最终选择、fallback 原因与预算影响写入 State/Trace。
6. 增加 Provider 暂不可用、预算不足、能力不匹配、fallback 耗尽和无模型 Direct 路径测试。

### 依赖关系

- 依赖 Task2 的 Model Decision Schema；依赖 Task3 的预算预检接口。

### 验收标准

- 仅选择 Registry 中已启用模型，配置中不存在的模型不会被构造或调用。
- 相同能力快照和策略输入得到稳定、可解释的候选过滤结果。
- fallback 每次切换均有原因和预算预检，不形成无限模型切换。
- 高风险 Fixture 即使选择更强模型也仍进入 Gate/Human Review。
- 配置和 Trace 中不出现 API Key 或其他秘密。

### 预估复杂度

- 中（2–3 人日）。

## Task5：实现 Execution Router、规则优先级与 Human Review 分流

### 任务目标

实现只做决策、不执行 Tool 的 Router，稳定输出 `route`、`confidence`、`reason`、`required_capabilities`、`risk_level` 和 `fallback`，并正确处理缺输入、低置信度、冲突规则和高风险优先级。

### 子任务

1. 定义并实现 Route Decision Schema、规则输入和 Direct/Workflow/Tool Loop/Plan-Delegation/Human Review 的进入、退出、降级与回退条件。
2. 固化优先级：不可逆/高风险与强制 Gate 规则优先，其次输入完整性和冲突，再判断复杂度、能力与预算。
3. 为低置信度、缺少关键输入、规则冲突和未知能力生成具体澄清问题或 Pending Action，不硬猜。
4. 确保 Router 只读取能力/风险/预算快照，不直接调用 Tool、创建 Child、启动 Workflow 或写外部系统。
5. 简单解释/确认走 Direct；固定求职周报走 Workflow；单个 JD 读取走 Tool Loop；复杂批量调研才允许 Plan/Delegation。
6. 记录 `route_decision_id`、命中规则、置信度来源、风险证据、fallback 和 Model Decision 关联。

### 依赖关系

- 依赖 Task2 的 Route Decision/State Schema、Task3 的预算视图和 Task4 的 Model Router。

### 验收标准

- 指定路由 Fixture 均得到预期 route、理由、能力、风险和 fallback。
- 简单问答不生成 Plan、不启动 Task Manager/Child，且没有 Tool 调用。
- 发送求职邮件、投递和外部数据修改在执行前进入 Human Review/Approval。
- 低置信度、输入缺失与规则冲突返回可操作问题；不会凭默认值继续高风险动作。
- Tool 关闭时可安全降级、询问或停止，并明确缺失能力；Router 本身零 Tool 调用。

### 预估复杂度

- 高（3–4 人日）。

## Task6：接入 Pending Action 与现有 Approval Gate

### 任务目标

让 Human Review 分支复用现有 Confirmation/Email Approval，保存可审计 Pending Action，并在批准、拒绝、过期或策略变化时做正确的条件恢复。

### 子任务

1. 将 Pending Action 绑定现有 confirmation/approval ID、action hash、策略版本、目标、风险、过期时间和恢复节点。
2. 高风险动作在 Gate 决定 `require_confirmation` 后进入等待，不提前执行 Tool 或写入外部系统。
3. 批准后重新校验 action hash、权限、策略、Tool 启用状态和预算，只恢复绑定节点而非重跑完整对话。
4. 拒绝、过期、内容变更和策略变更分别产生明确终止/重新确认结果。
5. 保证审批与 Unified Tool Executor 的 exactly-once/幂等语义沿用现有实现。
6. 在 API/事件层返回可供现有前端展示的 pending action，而不新增第二审批端点体系。

### 依赖关系

- 依赖 Task2 的 Pending Action/State 转移和 Task5 的 Human Review 路由；复用现有 Gate/Approval。

### 验收标准

- 发送求职邮件 Fixture 在批准前无外部发送，批准后仅执行一次。
- 修改动作内容会使旧批准失效；过期、拒绝和策略收紧均不能旁路 Gate。
- Resume 只恢复目标 Node，并再次经过预算与 Gate 条件。
- Pending Action 的敏感内容按现有脱敏规则存储和展示。
- 现有 Confirmation/Email Approval 回归全部通过。

### 预估复杂度

- 中（2–3 人日）。

## Task7：实现结构化 Planner 与确定性 Plan Validator

### 任务目标

仅为复杂开放任务生成带依赖 DAG 的结构化 Plan，并在任何执行前确定性校验权限、能力、依赖、循环、预算和不可逆动作。

### 子任务

1. 定义 Planner 输入和结构化输出；每个 Plan Step 包含 `goal`、`inputs`、`capabilities`、`done_when`、`risk`、`budget`、依赖和执行类型。
2. Planner 基于 Goal、最小 Context、能力快照和 Parent 预算分配生成 DAG，不执行 Tool、不创建 Child。
3. Plan Validator 检查 Schema、Step ID 唯一性、依赖存在、无环、输入可达、Tool/MCP 启用状态、权限、预算总和、不可逆动作与 Gate 要求。
4. 校验 `done_when` 可验证且输出契约明确；无法验证的模糊 Step 被拒绝并给出具体失败项。
5. 校验失败只允许有界重规划或询问用户；不得以自然语言解释代替 Validation Result。
6. 保存 `plan_id`、版本、Validation Result、模型决策、预算分配和失败代码。

### 依赖关系

- 依赖 Task2–Task5 的 Schema、Budget、Model Router 和路由边界。

### 验收标准

- 仅 `plan_delegation` 路由调用 Planner；Direct、Workflow、Tool Loop、Human Review 均不生成 Plan。
- 合法 Plan 可拓扑排序；缺依赖、重复 ID、自环和多节点循环均在执行前被拒绝。
- Tool/MCP 关闭、权限不足、预算超配和未标记不可逆动作返回字段级失败项。
- Planner 的每个 Step 均含需求规定的六类核心字段及可执行输出契约。
- Plan 被拒绝时没有任何 Step、Tool 或 Child 已启动。

### 预估复杂度

- 高（4–5 人日）。

## Task8：贯通 Context、Plan/Todo、Task Snapshot 与 Child Result 所有权

### 任务目标

在既有 Context 管理上明确 Goal、安全策略、当前 Plan、预算、Todo、长期记忆、Task Snapshot 与 Child Result 的边界，保证压缩后关键控制信息不丢失且 Child 不接收完整主对话。

### 子任务

1. 定义 Context Builder 各分区的数据所有者、写入者、生命周期、优先级和裁剪规则。
2. Summary/Trim 只压缩对话与可重建工作信息，永久保留 Goal、安全策略、当前 Plan/Step、预算状态、Pending Action 和关键 Artifact refs。
3. Todo 表达当前 Run 的操作进度，Plan 表达已验证 DAG；禁止创建另一套互相漂移的计划来源。
4. 长期记忆只通过既有 Memory 服务读取引用；敏感数据、审批内容和 Child 私有工作上下文不自动写入长期记忆。
5. Task Snapshot 保存后台控制面最小快照；Child Result 只通过统一 Result Envelope/Artifact refs 回填 Parent。
6. 为长对话裁剪、Plan 更新、Child 隔离、Prompt 注入边界和 Artifact 缺失补充测试。

### 依赖关系

- 依赖 Task2 的 State Schema、Task7 的 Plan 契约；复用现有 Context/Memory/Todo。

### 验收标准

- 多轮 Summary/Trim 后 Goal、安全策略、Plan、当前 Step 和预算仍可精确恢复。
- Parent 不接收完整 Child 消息历史、scratchpad 或未经验证的内部上下文。
- Todo 与 Plan 有单向映射/引用规则，不出现两个权威 Plan。
- Child Result 仅含契约字段、来源/引用和 Artifact refs，超大正文不直接注入主上下文。
- 现有 Context、Memory、隐私与 Token 回归全部通过。

### 预估复杂度

- 高（3–4 人日）。

## Task9：接入统一 Executor 与显式条件状态转移

### 任务目标

通过 Adapter 将 Direct、Workflow、Tool Loop 和已验证的 Plan/Delegation Step 接入唯一 Executor/AgentRuntime，并按条件 Edge 驱动状态，而非构造每次必经的大链。

### 子任务

1. 实现 Orchestration Controller/Executor Adapter，基于当前 route/step 选择确定性回复、现有 Workflow、现有 AgentRuntime Tool Loop 或 Delegation Adapter。
2. 每次 Node 执行前检查 Budget、取消、Gate、能力和输入条件，执行后更新 outputs/artifact_refs、current_step 和终止原因。
3. Direct 路径支持无需 Tool 的解释/确认并直接 End；Workflow 保持固定步骤和既有规则；Tool Loop 保留动态 Tool 观察循环。
4. Plan 前台 Step 仅在 Plan Validator 通过后按拓扑顺序执行；需要 fan-out/后台时交给 Task Manager，而不是 Executor 自建调度器。
5. 定义各路径的失败、降级和 fallback：Tool 不可用、Workflow 不匹配、Plan 无效、预算不足和 Gate 拒绝。
6. 使用功能开关接入现有 API 入口，关闭时完整回退到现有行为。

### 依赖关系

- 依赖 Task2–Task8 的状态、Budget、Model Router、Router、Approval、Plan 和 Context 契约。

### 验收标准

- 五类 route 只进入各自必要 Node，Trace 能证明未发生无关 Planner/Delegation/Judge 调用。
- 所有 Tool 仍由唯一 AgentRuntime/Unified Tool Executor 执行并经过 Pre-Tool-Call Gate。
- 固定求职周报继续复用现有 Workflow；读取单个 JD 继续复用现有 Tool Loop。
- 功能开关关闭时现有聊天、Workflow 和 Tool Loop 行为与基线一致。
- 取消、预算耗尽和 Gate 拒绝均产生稳定停止原因，不继续隐式调用。

### 预估复杂度

- 高（4–5 人日）。

## Task10：实现前台/后台任务模式与生命周期 API

### 任务目标

在现有 Run Store/Dispatcher 上提供明确的前台与后台边界；后台任务创建后立即返回 `task_id`，并以权威持久化状态驱动查询、事件和运行详情。

### 子任务

1. 定义前台适用条件、同步等待上限和转后台规则；定义后台适用的批量、长耗时、等待外部资源或 fan-out 场景。
2. 后台创建采用幂等键，持久化 Background Task/Parent Run 后立即返回 `task_id`、初始状态和查询入口。
3. 实现 `queued`、`running`、`waiting`、`partial`、`completed`、`failed`、`cancelled`、`interrupted` 的合法迁移、时间戳和停止原因。
4. 扩展现有 API/SSE 的创建、查询、取消和事件契约；前端/连接断开不改变权威任务状态。
5. 进程启动时识别失去 lease/心跳且不可安全续跑的运行，明确标记 `interrupted` 或 `failed`；不从任意 Step Checkpoint 恢复。
6. 保证旧 API 字段兼容，并为重复创建、查询不存在任务、取消竞态和终态重放补充测试。

### 依赖关系

- 依赖 Task2 的 Background Task/状态迁移、Task3 的预算和 Task9 的 Executor 入口。

### 验收标准

- 后台批量调研请求在任务落库后立即返回稳定 `task_id`，不等待模型或 Child 完成。
- 八种任务状态均有明确定义、合法前驱和终态行为；非法回退由 CAS/规则拒绝。
- 客户端断线后重新查询得到 Run Store 的真实状态，而非静态占位或浏览器缓存。
- 模拟进程中断时任务变为 `interrupted/failed`，文档和测试均不承诺步骤级 Checkpoint 或跨重启原节点恢复。
- 重复幂等请求不会创建两个后台任务。

### 预估复杂度

- 高（4–5 人日）。

## Task11：实现 Plan DAG 调度与串并行资格判断

### 任务目标

从已验证 Plan 构建可执行依赖 DAG，并仅在输入独立、无共享写冲突、Result Envelope 统一且预算、并发和 Provider/站点限流允许时并行。

### 子任务

1. 实现拓扑层、ready set、依赖完成和失败传播计算；运行时再次防御循环和缺依赖。
2. 定义 Step 读/写集合、输入来源、输出契约、Provider/站点键和并行组。
3. 实现并行资格决策：输入是否已就绪、是否存在共享写冲突、结果是否可统一 Merge、预算是否可预留、并发槽与限流是否允许。
4. 不满足资格时稳定降级为串行或 waiting；记录每个并行/串行决定的具体原因。
5. 支持三个独立 JD 并行读取，以及 JD 与简历证据并行搜集；存在共享外部写入时强制串行并进入 Gate。
6. 增加 DAG 宽度、限流、backpressure、公平性和大 Plan 上限测试。

### 依赖关系

- 依赖 Task3 的预算、Task7 的 DAG/Validation、Task9 的 Executor 和 Task10 的后台任务契约。

### 验收标准

- 三个独立 JD Fixture 可并行启动，且并发数不超过配置上限。
- JD 与简历证据搜集可并行，Join 前不存在依赖未满足的消费。
- 共享写、结果契约不一致、预算不足或站点限流均阻止并行，并保存可解释原因。
- 计划循环即使绕过静态构造也在执行前被拒绝。
- backpressure 下任务进入 waiting/queued，而不是忙循环或无限创建 Child。

### 预估复杂度

- 高（4–5 人日）。

## Task12：实现 Parent Run / Child Run fan-out、隔离任务包与 fan-in

### 任务目标

把可并行或专长型 Step 通过现有 Delegation 转换为隔离 Child Run，并以最小 Task Contract、统一 Result Envelope 和 Artifact 引用安全汇合到 Parent。

### 子任务

1. 将已验证 Child Step 转换为最小任务包：goal、必要 inputs/artifact refs、允许 Tool 视图、预算、deadline、输出契约和 trace context。
2. 为每个 Child 创建稳定 `child_run_id`、Parent/Step 关联和隔离 `RunContext`，不复制完整主对话。
3. Child 只能访问显式允许的 Tool/MCP 和 Artifact；继承安全策略但不能扩大权限或预算。
4. Child 产出结构化 Result Envelope，包含 status、output/artifact refs、sources/citations、usage、errors 和 contract version。
5. fan-in 只接收持久化且通过基本 Schema 校验的 Child Result；冲突、缺失和超大输出进入 Merge/Verify 规则。
6. 保持现有第 10 阶段 Delegation 入口与测试契约，不新建第二套 Multi-Agent Runtime。

### 依赖关系

- 依赖 Task8 的 Context 边界、Task10 的 Parent/后台持久化和 Task11 的 fan-out 资格判断。

### 验收标准

- Child 任务包不含完整 Parent 对话、无关记忆或未授权 Tool。
- Child 预算和 deadline 不超过 Parent 分配，权限只能收窄不能扩大。
- 三 Child Fixture 生成唯一且可关联的 Parent/Child/Step ID，结果均符合统一 Envelope。
- Parent 只读取 Result Envelope 与 Artifact refs；Child scratchpad 不进入 Parent Context/Trace。
- 现有 Delegation 单元和集成回归保持通过。

### 预估复杂度

- 高（4–5 人日）。

## Task13：实现事件驱动 Task Manager 与并发治理

### 任务目标

由 Task Manager 管理 Child 启动、并发、deadline、取消、有限重试和状态更新；Child Runtime 通过结构化事件通知，不允许主 Agent 以反复模型调用轮询完成状态。

### 子任务

1. 定义并持久化 `child_started`、`child_progress`、`child_completed`、`child_failed`、`child_cancelled`、`child_timed_out` 事件及稳定 `task_event_id`。
2. 实现事件幂等、sequence/version、乱序缓冲或忽略、迟到事件隔离、重复完成拒绝和终态不可逆规则。
3. 接入现有 Dispatcher/Worker/lease/heartbeat，实施全局、Parent、Provider/站点和 Tool 维度并发限制与 backpressure。
4. 实现 deadline 计时、父到子取消传播、已启动 Tool 的既有取消语义和取消竞态治理。
5. 只对显式可重试的瞬时失败执行有限重试；重试复用逻辑任务 ID、生成新 attempt，并受剩余预算/deadline 限制。
6. Parent 由持久事件、容量释放、deadline 或用户/审批事件唤醒；增加断言确保模型调用不会用于状态轮询。

### 依赖关系

- 依赖 Task3 的 Child 预算结算、Task10 的任务生命周期和 Task12 的 Parent/Child 契约。

### 验收标准

- 六类 Child 事件可驱动权威状态更新并关联 Parent/Child/Step。
- 重复、乱序、迟到和重复完成事件不会覆盖合法终态或重复预算/结果记账。
- 并发和限流达到上限时新任务排队/等待，释放容量后由事件机制继续。
- Parent 取消可传播到未启动和运行中 Child；取消与完成竞态有确定结果。
- 静态检查与调用计数 Fixture 证明不存在模型轮询 Child 状态；重试次数严格有限。

### 预估复杂度

- 很高（5–7 人日）。

## Task14：实现 Join Policy、确定性 Merge 与 Parent 续跑条件

### 任务目标

实现 `all_required`、`partial_allowed`、`first_success`、`deadline_reached` 四类 Join Policy，明确 Child 失败、超时、取消和缺失结果怎样进入 Merge、Verify、Human Review 或 Stop。

### 子任务

1. 定义 Join Policy 配置、required/optional Child、成功阈值、deadline 和可接受缺失规则。
2. 实现 `all_required` 全部必要成功、`partial_allowed` 达到最小有效集、`first_success` 首个合格结果、`deadline_reached` 到期按已有结果决策的满足条件。
3. 每次相关 Child 终态或 deadline 事件后生成幂等 Join Decision，记录 included、failed、missing、cancelled、timed_out 与理由。
4. `first_success` 满足后按策略取消不再需要的 Child；迟到结果不改变已提交 Join，仅作为审计事件。
5. 实现确定性 Merge，保留来源、引用、冲突和缺失标记；不让自然语言模型隐式吞掉失败。
6. 将 Join 结果接入 Merge→Verify、Partial→Verify/Human、不可满足→Stop/Recovery 等条件 Edge。

### 依赖关系

- 依赖 Task11 的 DAG、Task12 的 Result Envelope 和 Task13 的事件/终态。

### 验收标准

- 四类 Join Policy 均有正常、失败、超时、取消和部分结果 Fixture。
- Parent 只在 Join 满足或需要明确决策时继续，不以轮询或固定 sleep 续跑。
- Child 的失败/缺失不会被 Merge 静默丢弃，输出可定位到 child_run_id 与 step_id。
- 汇合后三个 JD 排序保留每个候选的来源、有效性和缺失证据。
- 重复 Join 触发得到同一逻辑决定，不重复取消、Merge 或预算记账。

### 预估复杂度

- 高（4–5 人日）。

## Task15：实现 Runtime Verifier 与可选 Judge Rubric

### 任务目标

建立当前 Run 的在线验证节点，以确定性检查为权威并只在必要时调用可选 Judge；明确它与离线 Eval Runner 的输入、时机和动作边界。

### 子任务

1. 定义 Verify Result、failure code、path/step/source 定位、repairable、severity 和 next action。
2. 按顺序执行权限/Gate 结果、Schema、业务规则、来源可访问性、引用完整性、预算一致性和产品 Rubric 检查。
3. 权限、Schema、来源、引用和预算由确定性逻辑判定，不允许 Judge 覆盖或放宽。
4. 可选 Judge 只处理无法确定性表达的语义质量 Rubric；使用固定输入、Schema 输出、Model Decision 和预算上限。
5. Verify 结果只决定当前 Run 进入 End、Recovery、Human Review 或 Stop；不得决定版本发布。
6. 离线 Eval Runner 使用固定数据集比较版本并供 Release/Safety Gate 决策；不得参与线上状态转移。

### 依赖关系

- 依赖 Task4 的 Judge 模型选择、Task9 的输出契约和 Task14 的 Merge/Join 结果。

### 验收标准

- 引用缺失、Schema 错误、权限异常、预算不一致和业务规则失败均返回具体失败项和定位。
- Judge 关闭或不可用时，确定性验证仍完整工作；Judge 不能把确定性失败改成通过。
- Runtime Verifier 不写离线发布结论，Eval Runner 不驱动当前 Run 的 Edge。
- 无需验证的简单 Direct 可直接 End；需要验证的计划/合并输出必须经过指定 Verifier。
- Verify Trace 可关联 output/artifact、plan/step/child 和 Model Decision。

### 预估复杂度

- 高（4–5 人日）。

## Task16：实现只修失败项的 Bounded Recovery

### 任务目标

将 Verifier 的可修复失败映射为最小 Recovery Attempt，最多执行配置的 1–2 次修复，禁止无限 Reflection、全任务重跑和全文无差别重写。

### 子任务

1. 定义失败类型到修复器/目标字段/最小输入的映射，以及不可修复失败的 Stop/Human 路径。
2. Recovery 输入只包含失败项、相关输出片段、必要来源/Artifact 和剩余预算，不重新注入完整上下文。
3. 每次修复生成 patch/artifact ref、关联 failure IDs、attempt number 和实际预算消费。
4. 修复后只重跑受影响的确定性规则及必要 Judge Rubric；无关已通过项保持不变。
5. 达到 1–2 次上限、预算不足、风险升高或重复相同失败时立即停止、降级或交人工。
6. 防止全文重写、Planner 重启、Child 全量重跑和 Recovery→Reflection 自循环。

### 依赖关系

- 依赖 Task3 的预算条件和 Task15 的结构化失败项。

### 验收标准

- 单字段引用缺失只修该引用/证据，不改写已通过正文。
- `revision_count` 从统一 State 读取并严格不超过配置上限 1–2。
- 相同失败重复出现或变为不可修复时进入稳定 Stop/Human Review，不再调用模型/Tool。
- Recovery 后验证范围可由 Trace 证明只包含受影响规则。
- 测试断言不存在无限循环、全文无差别重写或全 Plan 隐式重跑。

### 预估复杂度

- 中高（3–4 人日）。

## Task17：贯通编排 Trace、关联 ID 与脱敏投影

### 任务目标

把 Route、Plan、Validation、Parent/Child、Task Event、Join、Verify、Recovery、Budget 和 Model Decision 投影到现有 Run Event/Trust Trace，并与现有 Run/Turn/Model/Tool/Approval 形成完整关联链。

### 子任务

1. 为各节点/条件边定义 Trace event type、开始/结束/失败语义、duration、decision reason 和 redaction contract。
2. 贯通 `route_decision_id`、`plan_id`、`step_id`、`parent_run_id`、`child_run_id`、`task_event_id`、`join_decision_id`、`verify_id`、`recovery_id`、`budget_snapshot_id`、`model_decision_id`。
3. 将业务 Run Event 作为权威状态事件，将 Trust Trace 作为脱敏可观测投影；禁止 Trace 反向修改业务状态。
4. 对重复事件和重试定义稳定 correlation/attempt 关系，确保不会重复计数或形成断链。
5. 记录停止原因、fallback、降级、验证失败和预算变化，但不记录秘密、完整 Child Context 或不必要个人数据。
6. 扩展 Trace 查询/导出测试，验证 Parent→Child→Join→Verify→Recovery 的链路完整性。

### 依赖关系

- 依赖 Task2–Task16 已稳定的 ID、事件和决策 Schema。

### 验收标准

- 任一编排 Run 可从 route_decision 追踪到最终 End/Stop/Human Review，且所有关键 ID 可双向关联。
- 并行 Child 的事件顺序可独立还原，Join Decision 能定位纳入与缺失结果。
- Budget 与 Model Decision 的每次变化都有对应快照/原因，但敏感配置被脱敏。
- Trace 投影失败不篡改业务状态；按既有策略重试或降级并留下诊断。
- 现有 Trust Trace、隐私和日志安全测试保持通过。

### 预估复杂度

- 高（3–5 人日）。

## Task18：扩展现有运行详情与调试 API

### 任务目标

在保留完整聊天展示的前提下，让现有运行详情基于真实 API/事件展示路由原因、Plan 依赖、后台任务、Child、Join、验证、修复、预算和停止原因。

### 子任务

1. 扩展运行详情 API/ViewModel，提供 Route Decision、Plan DAG、Background Task、Parent/Child、Join、Verify、Recovery、Budget 和 Model Decision 的脱敏投影。
2. 在现有详情页增加可折叠编排区域，保留聊天消息为主视图，不把调试信息混进对话正文。
3. 展示实时 `queued/running/waiting/partial/completed/failed/cancelled/interrupted`、Child 进度、attempt、deadline 和停止原因。
4. 可视化 Plan 依赖与串并行原因、Join Policy 及 included/failed/missing 结果。
5. 展示 Verify 失败项、Recovery 次数/目标、Budget 使用/上限和 Model Router 理由，不暴露秘密或 Child 完整上下文。
6. 使用真实 API/SSE 与重连补拉；删除或禁止静态 mock 状态作为生产详情来源。

### 依赖关系

- 依赖 Task10 的后台 API、Task13–Task17 的事件、Join、Verify、Recovery 与 Trace 投影。

### 验收标准

- 刷新或 SSE 重连后详情与 Run Store 一致，不丢失终态、预算或 Child 结果。
- 用户能从一个运行详情定位 route reason、Plan 依赖、Child 状态、Join Policy、失败项、修复次数、预算进度和停止原因。
- Direct 请求不会展示虚构 Plan/Child；无相关数据的区域不渲染静态占位。
- 完整聊天展示和现有运行详情功能无回归。
- 前端不展示 API Key、完整 Child Context、审批秘密或未脱敏个人数据。

### 预估复杂度

- 高（4–5 人日）。

## Task19：建立固定 Fixture、分层测试与离线评测集

### 任务目标

建立不依赖外部网络的固定评测集和完整测试矩阵，覆盖路由、计划、并发时序、事件幂等、汇合、部分失败、验证修复、预算与安全边界，并接入既有 Eval/Release 流程。

### 子任务

1. 建立固定 Fixture：简单问答、固定求职周报、单 JD、后台批量调研、三个独立 JD、JD 与简历证据、汇合排序、发送邮件、低置信度和 Tool 关闭。
2. 增加 Plan 拒绝 Fixture：缺依赖、循环、权限不足、MCP 关闭、预算超配、不可逆动作未声明和输出契约不可验证。
3. 增加并行时序 Fixture：乱序、重复、迟到、重复完成、deadline、取消竞态、限流/backpressure 和有限重试。
4. 增加 Join/部分结果 Fixture：四类 Join Policy、Child 失败/超时/取消、缺失引用、冲突来源和部分排序。
5. 增加 Verifier/Recovery/Budget Fixture：确定性失败、Judge 不可用、只修失败项、修复上限、tokens/cost/time/tool_calls/steps 耗尽。
6. 按 Schema/策略单元、Runtime/Store 集成、API/前端 E2E、并发时序和离线 Eval 分层运行；固定随机种子、时钟和 Provider 响应。
7. 将离线 Evaluation 用于版本比较和 Release/Safety Gate，单列运行时 Verifier 测试，禁止二者互相替代。

### 依赖关系

- 依赖 Task5–Task18 的可观测实现和测试接口。

### 验收标准

- 需求列出的所有边界场景至少对应一个命名 Fixture，并有预期 route、状态、调用次数、结果和 Trace 断言。
- 单元、集成、E2E、并行时序、幂等、超时/取消、部分失败、回归和离线 Eval 均有独立报告。
- 固定集在无网络环境可重复运行，相同版本结果稳定；真实网络 Smoke 不影响固定 Release Gate 分数。
- 测试能检测 Planner/Delegation 误用于简单任务、模型轮询 Child、Gate 旁路、无限 Recovery 和预算超发。
- 评测报告明确区分产品失败、代码失败、环境阻塞和外部站点波动。

### 预估复杂度

- 很高（5–7 人日）。

## Task20：运行真实求职端到端 Smoke 与既有第 9、10 阶段关键回归

### 任务目标

在固定测试通过后，通过真实求职链路验证编排接入，并明确执行既有阶段文档中的第 9 阶段安全/Context/Trace 回归与第 10 阶段 Delegation/求职调研回归；此处不以本文 Task9/Task10 的编号替代既有阶段含义。

### 子任务

1. 使用真实入口运行简单问答、固定周报、单 JD 读取、三个 JD 并行调研、JD+简历证据汇合排序和后台批量调研。
2. 在受控测试目标上验证发送求职邮件的 Approval 等待、拒绝和批准后 exactly-once；不得向未授权真实收件人发送。
3. 运行既有第 9 阶段关键回归：隐私/日志脱敏、Context Token/Summary/Memory、Tool/MCP Result Guard、Gate 与 Trust Trace。
4. 运行既有第 10 阶段关键回归：Coordinator、job web researcher、Parent/Child Delegation、真实启动入口、Result Envelope、取消/重试及求职调研 E2E。
5. 通过现有 `agent trust real-smoke --run-id <id> --source-url <url>` 或 Task1 审计确认的等价真实入口运行 Search/Browser Smoke。
6. 保存 route/plan/child/join/verify/budget/stop 的脱敏 Trace、运行详情截图或 Artifact，以及 Provider/站点/环境元数据。
7. 外部 Provider、网络或 Playwright MCP 不可用时标记 `blocked` 并保留证据，不修改固定评测基线，也不伪装成功。

### 依赖关系

- 依赖 Task19 固定测试全部达到进入 Smoke 的门槛；依赖现有第 9、10 阶段能力可用。

### 验收标准

- 至少一条真实求职只读链路成功并可从 Route 追踪到最终结果；并行场景能看到真实 Child 与 Join。
- 高风险邮件场景证明 Approval 前零外部副作用，拒绝不发送，批准后最多发送一次。
- 既有第 9、10 阶段关键回归分别有命令、通过数、失败数和报告位置。
- 真实 Smoke 与固定 Fixture/Release Gate 分开报告，环境阻塞具有可复现错误和恢复步骤。
- 未因 Smoke 临时绕过 Gate、限流、预算、证书验证、隐私规则或 Tool allowlist。

### 预估复杂度

- 高（3–5 人日，外部环境阻塞时间不计入）。

## Task21：独立验收、修复失败并重跑相关全量测试

### 任务目标

由未参与对应实现判断的验收视角，逐项核对需求和设计，修复发现的问题，并重跑受影响范围及全量相关测试，形成最终可发布或不可发布结论。

### 子任务

1. 建立需求→设计→Task→代码→测试→Trace/界面证据的双向追踪矩阵，逐项核对 21 类实施要求和禁止项。
2. 独立审查 Runtime 唯一性、状态权威、Gate 不旁路、Budget 不超发、Context 隔离、事件幂等、Join 正确性、Recovery 上限和 Trace 脱敏。
3. 对失败按根因分类，先新增/固定复现测试，再做最小修复；不得以放宽断言、删除 Fixture 或提高预算掩盖问题。
4. 每次修复重跑直接相关单元/集成/E2E，再运行编排全量、既有 Runtime/Workflow/Tool/Gate/Delegation/Context/Trace/Eval 回归。
5. 对并发、时间和取消相关测试执行重复/扰动运行，排除偶发通过；对数据库迁移和旧数据读取执行升级/回滚验证。
6. 形成最终验收报告，列出通过项、遗留风险、环境阻塞、性能数据、开关默认值、回滚步骤和是否满足发布门槛。
7. 若仍有阻塞性失败，结论必须为不可发布并列出恢复方式；不得把运行时 Verifier 结果当作离线发布批准。

### 依赖关系

- 依赖 Task1–Task20 完成并提供实现、测试、Smoke 和审计证据。

### 验收标准

- 追踪矩阵覆盖全部已确认需求，且每项均有可复现证据或明确阻塞原因。
- 所有确定性固定测试和关键回归通过；外部 Smoke 阻塞被独立记录且不污染固定基线。
- 失败修复均有回归测试，相关全量测试在修复后重新运行并保存报告。
- 性能、安全、预算、并发、取消、部分结果和前端动态展示均达到设计验收门槛。
- 最终报告明确发布/不发布结论、功能开关启用顺序与可执行回滚方式；未实现或暗示 Checkpoint/Interrupt、第二 Runtime 或全面框架迁移。

### 预估复杂度

- 高（4–6 人日，取决于验收失败数量）。

## 2. 总体验收顺序

1. Task1–Task8 先冻结边界与核心契约，任何 Schema/权威数据源变化必须先回写已确认设计并重新评审。
2. Task9–Task18 完成运行时、后台/并行、验证恢复、Trace 与前端闭环；每个阶段都必须保持功能开关可回退。
3. Task19 先通过固定 Fixture 与离线评测，Task20 才运行真实外部 Smoke。
4. Task21 独立验收后才允许提出默认启用或发布；本文档生成本身不代表开始执行。

收到“确认计划，开始执行”后，才按 Task1 → Task21 的依赖顺序修改代码并逐项报告证据。
