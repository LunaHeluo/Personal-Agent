# 求职调研有边界任务委派需求

## 文档信息

- 文档阶段：第一阶段需求澄清
- 审查基线：2026-08-10 当前工作区；以真实代码而非示例提示词为准
- 目标方案：受控纵向切片，首版仅引入 `job_web_researcher` 与 `profile_evidence_analyst`
- 已确认决策：持久化后台 Run、完成后自动回填原 Chat、五维硬预算、协作式取消并保留审计证据、量化 Multi-Agent 启用门槛
- 本阶段不包含代码、数据库迁移、API 实现、前端实现或实施计划

## 1. 需求背景

目标场景是：用户要求调研悉尼的 Agent 工程师岗位，并结合授权简历证据生成投递优先级、匹配依据和能力缺口。该任务同时包含多页面动态网页调研、受限简历证据检索、结构化校验与确定性合并，存在上下文污染、工具越权、重复抓取、预算失控和结果静默补写等风险。

当前仓库已经具备可复用基础：

- `AgentRuntime` 提供受限的多轮 Model → Tool → Observation 循环，并具有模型调用次数、工具调用次数、运行时间和工具超时限制。
- `UnifiedToolRegistry`、`PreToolCallGate`、`UnifiedToolExecutor`、确认机制和 Playwright 网络保护已经形成统一工具治理路径。
- `search_jobs_serpapi`、Playwright MCP、`retrieve_resume_evidence`、`job-research` Skill、知识库/RAG、Tool Artifact、Context Summary/Trim 已存在。
- Trust 层已有固定 Fixture、Eval Runner、真实 Smoke、Trace Store、Safety/Release Gate 以及 Trust Center 页面基础。

但当前求职调研仍由 API 内固定 Workflow 在单次 Chat 请求中直接串行驱动 `JobResearchOrchestrator`。当前没有持久化业务 Parent/Child Run、独立 `RunContext`、Specialist Registry、业务 Run 查询/取消 API，也没有后台任务恢复和完成后幂等回填机制。`TrustTraceEvent` 虽已有可选 `child_run_id` 字段，但尚未形成真实父子业务 Run 树。现有 Token 配置主要表示模型上下文窗口和 Session 用量，不是可在 Parent/Child 间预留、扣减和归还的任务预算；费用预算也尚不存在。

因此，本需求不是简单把固定 Workflow 放入后台，也不是让多个模型自由聊天，而是在复用现有 Runtime、Gate、Trace、预算计量和评测体系的前提下，建立可验证、可取消、可追踪、最小权限的真实任务委派边界。

### 1.1 关键现状审计

| 优先审计项 | 真实仓库现状 | 本需求结论 |
|---|---|---|
| 异步运行模型 | 求职 Chat 在 HTTP/SSE 请求内 `await` 固定 Workflow；Stream 使用进程内 Task/Queue。自动记忆是进程内后台 Task；Eval Runner 可用 `asyncio.gather` 并发 | 业务委派必须新增持久化后台 Run 语义，不能直接复用这些易随进程/连接丢失的 Task 作为业务真相 |
| 任务状态 | 只有 Eval Run/Case 状态和 Skill 字符串状态；没有通用 Parent/Child 业务状态机 | 建立持久化、版本化 Parent/Child 状态与恢复规则 |
| Trace ID | Trust Trace 已关联 Eval/Case/Session/Turn/Model/Tool/Policy/Approval，并预留可选 `child_run_id`；没有完整 `parent_run_id` 业务树 | 扩展现有 Trace 模型和查询，不另建 Trace；复用 `child_run_id` 并补齐 Parent/Task 关联 |
| 预算 | Runtime 有模型/工具调用次数、总时间、Tool timeout；Context 有 Token 窗口/Session usage；JD Workflow 有 retrieval seconds | 在同一计量基础上增加可分配的五维 Run 预算账本；不把 Context 窗口冒充任务 Token 预算 |
| 取消 | Eval Runner 只有实例内布尔取消标记；Chat/求职 Workflow 无持久化取消 API；Trust UI 取消按钮为禁用占位 | 增加 Parent 取消 API、持久化取消信号和 Child 协作式传播 |
| 前端运行详情 | Chat 仅消费 Tool Event/最终 `ChatResult`；Trust Center 能列 Eval/Trace/Safety，但没有业务父子树。UI 的“运行固定评测”目前只 POST 创建 Eval Run 记录，不驱动 Eval Runner 执行 | 任务卡和 Trust 详情必须读取真实后台状态；任何“运行/取消”控件必须连接真实执行能力 |
| 测试入口 | pytest 单元/集成/E2E、Trust Fixture CLI、Real Smoke CLI 已存在 | 在现有 pytest/Eval Runner/Safety Gate 内扩展，并严格分离 Fixture 与真实 Smoke |

## 2. 功能范围

### 2.1 范围内

- 建立单 Agent 基线与 Multi-Agent 候选方案的量化比较门槛；评测结论允许为“不采用 Multi-Agent”。
- 建立持久化 Parent Run、Child Run、Child Task、状态机、租约/心跳、幂等回调、自动回填和取消传播的需求契约。
- 建立 Specialist Registry，并首批注册 `job_web_researcher` 与 `profile_evidence_analyst`。
- 为 Coordinator 提供内部 `delegate_task(specialist_id, task_contract)` 委派入口；该入口可呈现为 Tool Call，但必须由后端创建真实 Child Run。
- Parent 与 Child 复用现有 `AgentRuntime / AgentLoop` 代码路径，每次运行创建独立 `RunContext`。
- 对不同角色执行最小上下文、最小工具 Schema 和最小数据权限隔离。
- 将多页面、动态页面、需持续观察与异常处理的 JD 网页调研迁移到 `job_web_researcher` 唯一主路径。
- 复用现有 Search、Playwright MCP、RAG、job-research Skill、Pre-Tool-Call Gate、Trace、Artifact、Eval Runner 与 Safety Gate。
- 在原 Chat 显示真实后台任务状态，在 Trust Center 显示父子任务树、预算、失败原因和合并结果。
- 建立固定 Fixture 评测与真实 Search/Browser Smoke 的独立记录和发布判定。

### 2.2 范围外

- 不新增第二套 Agent Runtime、Agent Loop、权限 Gate、Trace Store 或互不兼容的预算系统。
- 不允许 Subagent 自由互聊或把自然语言对话当成任务契约。
- 首版不允许 Child 递归委派其他 Child。
- 不允许 `profile_evidence_analyst` 修改简历、补写经历或访问未授权知识库。
- 不允许 `job_web_researcher` 接收完整主 Chat、简历正文、投递计划、长期记忆或其他无关 Child 结果。
- 不绕过登录、验证码、站点权限、robots、网络范围或 Pre-Tool-Call Gate。
- 不在本阶段确定具体队列产品、数据库表结构、Worker 部署拓扑或 UI 视觉稿。
- 不在本阶段实现投递、邮件发送或外部写操作。

### 2.3 Multi-Agent 启用边界

Multi-Agent 不是预设答案。首版必须先以当前单 Agent/固定 Workflow 形成可重复基线，再运行相同 Fixture、相同模型与版本配置下的候选方案：

- “有效 JD 完整率”“来源可追溯率”“简历证据忠实度”至少一项提升不低于 10 个百分点。
- 其余两项质量指标不得下降，Safety Gate 不得出现新增失败。
- 总费用不得超过基线 1.5 倍，P95 端到端延迟不得超过基线 2 倍。
- 固定 Fixture 是发布判定依据；真实 Search/Browser Smoke 只单独报告，不混入固定基线。
- 未满足全部门槛时，正式路由保持单 Agent，Multi-Agent 标记为未采用；不得为了完成项目而降低阈值或隐去失败指标。

## 3. 目标用户与使用场景

### 3.1 目标用户

- 求职者：希望得到有来源、与本人证据严格对应的岗位优先级和能力缺口。
- 本地 Agent 使用者：希望在原 Chat 中发起、查看、取消任务，无需理解 Child Run 或多 Agent 内部细节。
- 运维/评测人员：需要在 Trust Center 中定位父子 Run、预算消耗、失败、权限决策和合并证据。
- 开发者：需要通过稳定契约添加或停用 Specialist，而不复制 Runtime 或绕过 Gate。

### 3.2 核心场景

1. 用户在原 Chat 提出“调研悉尼 Agent 工程师岗位，并结合我的简历给出投递优先级、匹配依据和缺口”。
2. 系统创建持久化 Parent Run，自动回填一张可刷新、可取消的任务卡。
3. Coordinator 根据已注册 Specialist 和剩余预算创建至少两个受控 Child Task：网页岗位事实调研、授权简历证据分析。
4. `job_web_researcher` 搜索候选 JD，并在受限多轮循环中处理动态页面、详情展开、翻页、异常与完整性检查。
5. `profile_evidence_analyst` 仅按授权引用加载简历 Chunk，输出可引用证据及缺失项。
6. Coordinator 校验 Result Envelope、去重并确定性合并；冲突和缺失保持显式。
7. Parent 成功或部分成功后，服务端幂等写入一条最终 Assistant 消息；用户仍只与原 Chat 交互。
8. 用户可随时取消 Parent；系统协作式停止新任务并传播取消信号，保留取消前证据但不再合并或回填结果。

## 4. 用户故事

- 作为求职者，我希望岗位结论带有可访问的来源 URL，以便核实岗位真实性和时效性。
- 作为求职者，我希望匹配依据只引用我的简历证据，以免系统虚构经历。
- 作为求职者，我希望刷新页面后任务仍存在，并能看到真实进度、失败和预算，而不是前端模拟状态。
- 作为求职者，我希望取消任务后不再产生新抓取、模型费用或最终回填。
- 作为求职者，我希望部分站点失败时仍能看到明确的 partial、missing 和 errors，而不是模型补齐失败字段。
- 作为运维人员，我希望从 Parent Run 追到每个 Child、Tool、Policy、Approval、Artifact 和 Eval Case，以便复盘。
- 作为开发者，我希望新增 Specialist 时只注册稳定契约并复用现有 Runtime/Gate，而不是复制一套循环和权限逻辑。
- 作为产品负责人，我希望用固定基线证明 Multi-Agent 的净收益；如果单 Agent 更好，系统应明确保持单 Agent。

## 5. 功能需求

### 5.1 Coordinator 职责与委派入口

1. Coordinator 仅负责意图确认、任务拆分、分配、预算预留、取消传播、结果收集、Schema/证据校验、去重、冲突保留和最终合并。
2. Coordinator 不得代替 Specialist 完成全部网页调研或简历证据检索，也不得在 Child 失败后用自身模型猜测缺失字段。
3. 默认只有 Coordinator 的有效 Tool View 包含 `delegate_task`；任何 Child 的 Tool View 均不得包含该入口。
4. `delegate_task(specialist_id, task_contract)` 必须调用持久化 Run 服务，原子创建 Child Task 与 Child Run，并返回至少 `task_id`、`child_run_id`、初始状态和已预留预算。
5. 普通 Python 函数调用、固定 Workflow 返回值、Mock 静态对象或仅写一条 Trace 事件，不得被标记为真实 Subagent。
6. Child Run 必须实际执行独立 System Prompt、Context、Tool 集、预算与受限多轮 Model → Tool → Observation。
7. Coordinator 不得临时覆盖 Registry System Prompt、扩大 Tool 权限、修改输出 Schema 或申请超过 Parent 剩余量的预算。

### 5.2 Specialist Registry

每个 Registry 记录必须至少包含：

- 稳定 `specialist_id`
- 独立 System Prompt 或受版本控制的 Prompt 引用
- 能力标签
- 允许 Tool 列表
- 输入 Schema 与输出 Schema
- Specialist 版本、Prompt 版本、Tool Schema 版本
- 启用/停用状态与停用原因
- 默认预算和每维最大预算
- 默认并发限制、超时、重试与 failure behavior
- 允许的 knowledge scope 类型和 Artifact 类型

Coordinator 只能委派已注册、启用、依赖健康且通过当前 Policy 的 Specialist。Registry 在 Child 创建时生成不可变快照，运行中的 Child 不受后续配置热更新影响；Trace 必须记录实际快照版本。

### 5.3 Parent Run、Child Run 与状态

1. Chat 接受请求后创建持久化 Parent Run，不得把浏览器长任务绑定为只能依赖原 HTTP/SSE 连接存活的进程内任务。
2. Parent 与 Child 状态以持久化后端为唯一事实来源。前端不得通过定时器或本地变量伪造完成、失败或预算状态。
3. 至少支持以下状态：
   - 非终态：`created`、`queued`、`running`、`waiting_for_user`、`cancelling`
   - 终态：`succeeded`、`partial`、`failed`、`timed_out`、`cancelled`、`budget_exhausted`
4. 状态转换必须单向、带版本号并可追踪；终态不得被迟到回调改写。
5. 后台 Worker 必须使用持久化租约/心跳或等价恢复机制。进程重启后，未完成 Run 可安全重领或进入明确失败状态，不得永久停留在 `running`。
6. Parent 状态由真实 Child 状态与合并结果推导；一个 Child 失败不必自动使 Parent 失败，须按 `failure_behavior` 产生 `partial` 或 `failed`。
7. 初始 Chat 响应应保持现有 `ChatResult` 兼容，并以新增可选字段或版本化契约返回 `parent_run_id`、`status` 和任务卡数据。
8. 成功或部分成功后，服务端向原 Session/Turn 关联的 Chat 幂等回填一条最终 Assistant 消息。回填键至少绑定 `parent_run_id + result_version + message_kind`，重复回调不能产生重复消息。
9. 已取消、失败、超时或预算耗尽的 Parent 不自动回填业务结论；任务卡仍展示终态、失败原因和可用的审计摘要。

### 5.4 标识与关联

1. Parent 必须具有稳定 `parent_run_id`；每个 Child 必须具有稳定且全局唯一的 `child_run_id` 和 `task_id`。
2. 每个 Trace Event 继续使用稳定事件 ID，并关联可获得的 `eval_run_id`、`case_id`、`session_id`、`turn_id`、`model_request_id`、`tool_call_id`、`policy_decision_id`、`approval_id`、`parent_run_id`、`child_run_id` 和父事件。
3. Child 使用内部 Turn/模型请求标识，但必须关联用户原始 Session 和发起 Turn；不得把 Child 对话作为普通 Chat 消息保存。
4. 现有 `TrustTraceEvent.child_run_id` 应复用；需要扩展 Parent 关联时应做兼容迁移，不得另建孤立 Trace 系统。
5. 所有状态、回调、直接写入和重试必须携带幂等键；相同幂等键、相同 payload 返回原结果，不同 payload 必须报冲突。

### 5.5 Child Task Contract 与字段所有权

每个 Child Task 的最终有效契约必须具有：

- `task_id`
- `specialist_id`
- `goal`
- `inputs`（优先引用，不复制大正文）
- `constraints`
- `output_schema`
- `allowed_tools`
- `deadline`
- Token、费用、墙钟时间、模型调用次数、工具调用次数预算
- `failure_behavior`
- `parent_run_id`
- `idempotency_key`

字段所有权如下：

| 所有者 | 可提供字段 | 不得提供或覆盖 |
|---|---|---|
| Coordinator | `specialist_id`、`goal`、必要 `inputs`、`constraints`、`failure_behavior`、请求预算 | System Prompt、Registry 版本、扩大后的 Tool 权限、超过 Parent 剩余量的预算 |
| Specialist Registry | System Prompt、能力、允许 Tool、输入/输出 Schema、版本、默认与最大限制 | Parent/Child ID、运行时剩余预算、用户授权范围 |
| Runtime | Parent/Child/Task ID、最终 deadline/预算、Policy、Trace Context、幂等键、取消信号、有效 Tool View | 业务结论或缺失字段补写 |
| Context Builder | 按引用和权限加载必要 Artifact/Chunk，生成最小上下文包 | 完整主 Chat、完整长期记忆、无关 Child 结果或全部 Tool Schema |

最终 `allowed_tools` 必须是“Registry 允许范围 ∩ 当前 Policy ∩ Task Contract 请求范围 ∩ 依赖健康状态”的安全交集。任一层拒绝即不可见、不可调用；不只是执行时拒绝，完整 Schema 也不得进入该角色的模型请求。

### 5.6 RunContext 隔离与基础设施复用

1. Parent 和所有 Child 必须走同一套现有 `AgentRuntime / AgentLoop` 实现；允许对现有 Runtime 做通用化重构，但不得复制第二套循环。
2. 每次 Parent/Child 执行都创建新的独立 `RunContext`，不得复制或复用 Parent Agent 对象及其可变状态。
3. 每个 `RunContext` 独立持有 `messages`、working memory、todo/plan、effective tool view、五维预算账本、取消信号、summary/trim 状态和输出缓冲。
4. Parent、不同 Child 与重试 Attempt 之间不得发生消息、计划、工具视图、预算或输出缓冲串写。
5. Model Client、Tool 实现、Specialist/Tool Registry、Trace Store、Artifact Store、RAG 服务、Gate 和定价配置可以作为无会话可变状态的公共基础设施复用。
6. 跨 Run 资料优先使用 `artifact_id`、`knowledge_scope`、`document_id`、`chunk_id`、`source_url` 等引用，由 Context Builder 按权限加载必要片段。
7. 不得为了方便将完整 JD、简历、主会话或其他 Child 结果复制到每个 Context。

### 5.7 角色与工具隔离

| 角色 | 允许能力 | 明确禁止 |
|---|---|---|
| 主 Agent / Coordinator | `delegate_task`、结果 Schema/证据检查、确定性合并、用户确认 | 在多页面求职调研路径直接看到或调用完整 Search/Browser/RAG Tool Schema；代替 Specialist 补做任务 |
| `job_web_researcher` | `search_jobs_serpapi`、经现有 Gate 管理的 Browser/Playwright 能力；必要时受控单页读取底层能力 | RAG、简历、长期记忆、投递计划、委派入口、外部写 Tool |
| `profile_evidence_analyst` | 当前用户授权 knowledge scope 内的 `retrieve_resume_evidence`/RAG | Search、Browser、其他知识库、简历修改、经历补写、委派入口 |

所有 Tool Call，包括 Child 发起的 Tool Call，必须继续经过现有 `PreToolCallGate`、确认与 `UnifiedToolExecutor`。Child 不继承 Parent 的隐式权限，也不能因 Parent 已批准某类 Tool 而扩大自身契约。

### 5.8 `job_web_researcher` 行为契约

1. 初始上下文只包含 URL 或查询条件、目标字段、页面数量上限、停止条件、返回 Schema、必要安全约束和预算引用。
2. 先使用 Search 获取候选 JD 链接；再由同一个 Child 在受限多轮循环中持续执行：打开页面、等待动态渲染、观察页面、展开详情、进入必要详情页或下一页、提取字段、检查完整性并决定停止或继续。
3. 目标字段至少覆盖：岗位标题、公司、地点、职责、任职要求、来源 URL、最终 URL、抓取时间、页面/验证状态；无法确认的字段进入 `missing`。
4. 停止条件至少包括：达到有效 JD 目标数、达到候选/页面数上限、剩余预算不足以安全开始下一步、deadline 到达、取消、权限/人工处理阻塞、连续不可恢复失败。
5. 普通网页 Tool 与 Subagent 边界：
   - 单个稳定 URL、固定字段、一次调用即可返回时，可走明确的一次性单页 Tool 路径。
   - 需要跨页面持续推进、依据页面观察选择下一步、动态等待、异常恢复或压缩大量网页内容时，必须走 `job_web_researcher`。
6. 多页面求职调研中，`job_web_researcher` 是 JD 网页研究唯一主路径；符合条件的请求必须经 `delegate_task(job_web_researcher, task_contract)` 创建真实 Child Run。
7. 错误处理要求：
   - 页面加载失败、404、动态渲染超时、选择器失效、空正文、重复页面和可接受重定向：按配置有限重试、退避或更换候选入口，并记录每次 Attempt。
   - 登录、验证码、权限限制、robots 或站点明确拒绝访问：不得绕过。可进入 `waiting_for_user` 请求用户处理，或在不适合交互时返回 `partial`、`missing` 和明确错误。
   - 重定向必须重新经过网络范围与目标校验；跨站重定向保留原 URL 与最终 URL。
   - 重复页面按规范化 URL、内容 Hash 和岗位关键字段去重，不重复计为有效 JD。
8. 原始 HTML、Browser Snapshot、导航菜单、重复正文和中间页面不得进入 Parent Context。它们仅保留在受访问控制、按保留期清理的 Child Trace/Artifact 中。
9. 返回 Parent 的受控内容仅为标准化 `jobs[]`、`source_url`、`missing`、`errors`、`usage`、`child_run_id` 及必要 evidence 引用。

### 5.9 `profile_evidence_analyst` 行为契约

1. 仅接收目标岗位字段、授权 `knowledge_scope`、必要 `chunk_id`/查询条件、输出 Schema 和预算，不接收网页原始内容或完整主 Chat。
2. 仅从用户明确授权的简历知识库读取证据；每项匹配依据必须带 `chunk_id` 或等价稳定引用。
3. 不得把职位要求转换成用户经历，不得根据常识补写技能、年限、项目或教育背景。
4. 未找到证据时返回 `missing` 或能力缺口；不得生成看似合理的简历事实。
5. 输出至少包含岗位/要求引用、支持证据引用、匹配结论、证据强度、缺失项和冲突。

### 5.10 Result Envelope、共享写入与合并

1. Child 只向 Parent 返回受控 Result Envelope：`status`、结构化 `output`、`evidence`、`missing`、`conflicts`、`usage`、`child_run_id`；可附 `errors` 与契约版本。
2. 完整 Child 对话、隐藏推理、原始日志、HTML 和 Snapshot 不进入主 Agent Context。
3. Runtime 在接受 Envelope 前必须执行 JSON Schema、大小、来源、权限、预算和 ID 关联校验。Schema 不合法视为 Child 失败或按策略进行一次受限修复；Coordinator 不自行解释非法结构。
4. Child 对共享业务数据默认只写候选区；Coordinator 校验后再合并到 Parent 结果或业务存储。
5. 必须直接写入时使用版本号、锁或幂等键，防止并发覆盖和重复写入。
6. 合并必须保留 `task_id`、`child_run_id`、`source_url`、`chunk_id`、缺失项和冲突；不同来源的矛盾事实并列保留并标记，不静默覆盖。
7. 结果去重与排序应尽量确定性：规范化 URL/岗位标识去重，以明确规则计算投递优先级；模型可生成解释，但不得改变证据事实或补齐失败字段。
8. 若一个 Child 成功、一个失败，Coordinator 按 `failure_behavior` 生成 `partial`，只输出可验证部分并明确缺失影响。

### 5.11 五维预算、并行、超时与取消

1. Parent 和每个 Child 均具有 Token、费用、墙钟时间、模型调用次数、工具调用次数五维硬预算。
2. 最终 Child 预算不得超过 Registry 最大值、Task 请求值、Policy 限制和 Parent 剩余量中的最小值；创建 Child 时原子预留，结算后按实际使用释放未用额度。
3. 所有 Child 的累计实际使用和在途预留不得超过 Parent 总预算；并行启动前必须先成功预留全部维度。
4. 费用按版本化 Provider/Model 定价和真实 Token usage 结算；Provider 未返回费用时可由同版本价格表估算。既无可靠 usage 又无可审计保守上界时，不得把费用记为 0，应在启动前阻止该模型用于硬费用预算任务，或采用经配置批准的保守上界。
5. 达到任一硬预算后停止新的 Model/Tool 调用，传播取消，保留已完成证据，并以 `budget_exhausted` 结束相应 Run。
6. 支持有上限的并行；首版只允许 Coordinator 并行调度已注册 Child，Child 总数和并发数均由 Parent/Policy 限制。
7. 超时区分 Child deadline、Tool timeout、Model timeout和 Parent deadline；Child 超时不得自动延长 Parent deadline。
8. 幂等重试只针对可恢复错误，必须复用逻辑 `task_id`/幂等键并创建可区分 Attempt；不得重复计入已成功业务结果，但真实消耗必须计费。
9. 用户取消 Parent 时：
   - 原子进入 `cancelling`，停止创建或领取新 Child；
   - 向 queued/running/waiting Child 传播协作式取消信号；
   - Tool/Model 边界检查取消信号，并在安全点退出；
   - 已完成和取消前产生的候选证据保留在 Trace/Artifact 中；
   - 不再执行合并或自动回填业务结果；
   - 全部 Child 终止或超过取消宽限期后，Parent 进入 `cancelled`。
10. 默认禁止递归委派。未来若开放，必须另行定义最大深度、Child 总数、共享预算、循环检测和跨层取消，本需求不预留隐式递归。

### 5.12 Trace、Artifact、Eval 与 Safety 关联

1. 每个 Child Run 必须关联 Parent Run、Eval Case（评测时）、Session、Turn、每次 Model、Tool、Policy、Approval 与 Artifact。
2. Trace 至少记录：状态转换、任务契约 Hash、Registry 快照、有效 Tool View Hash、预算预留/扣减/释放、取消传播、重试 Attempt、结果校验、合并决策和自动回填。
3. Artifact Store 保存受限原始网页材料和中间结果；主 Trace 默认仅保存 Hash、安全摘要和引用，遵循现有脱敏策略。
4. Trust Center 必须能按 Parent/Child/Task/Session/Turn/Eval Case 过滤，并展示父子树、当前状态、开始/结束时间、五维预算、失败原因、缺失/冲突和合并结果摘要。
5. Trace/Trust Center 状态来自真实后端 Run Store；现有前端“取消运行（待后端支持）”占位必须由真实取消能力替换后才能启用。
6. 固定 Fixture Run 与真实 Smoke Run 使用不同 `run_type`、报告和聚合指标。Smoke 的网络波动不得污染固定发布 Gate。

### 5.13 前端运行详情与自动回填

1. 原 Chat 是唯一用户入口；不要求用户进入单独的 Subagent 对话。
2. 创建 Parent 后立即显示任务卡，至少包含：Parent Run ID、总体状态、已完成/总 Child 数、当前阶段、五维预算使用、开始时间、取消操作和查看详情入口。
3. 页面刷新或重新进入 Session 后，任务卡从后端恢复；SSE/轮询中断不改变后端执行状态。
4. 详情页展示 Parent/Child 树、Specialist ID/版本、状态、失败原因、missing/conflicts、来源摘要和预算；不得展示隐藏推理、完整 Child 对话或未脱敏原始日志。
5. `waiting_for_user` 时，任务卡明确说明是登录、验证码、权限或审批问题，并提供安全的继续/终止入口；不得自动尝试绕过。
6. Parent 成功或部分成功后，任务卡变为终态，同时由后端幂等回填最终 Assistant 消息。前端不得自行拼装最终业务结果。
7. 重复事件、乱序事件和重连必须依据 Run 版本/事件序号去重；旧事件不得覆盖新状态。

### 5.14 真实仓库旧入口审计与迁移影响

| 旧入口/位置 | 当前调用方与行为 | 当前输出契约 | 迁移影响 |
|---|---|---|---|
| `POST /v1/chat`、`POST /v1/chat/stream` in `src/starter_agent/interfaces/api.py` | 前端 `sendMessage()` 调用；先分类，`JOB_RESEARCH` 分支直接等待固定 Workflow。Stream 分支用进程内 `asyncio.create_task` 和 Queue 推送 Tool Event，仍依赖请求进程 | 最终 `ChatResult`；求职路径没有业务 `run_id`/任务状态 | 改为创建持久化 Parent；返回兼容 `ChatResult` 加任务卡/Run 引用。断线不取消后台 Run |
| `_dispatch_classified_chat()` | Router 的 `JOB_RESEARCH` 分支调用 `_chat_with_public_job_search_fallback()` | 同步返回完整或降级后的 `ChatResult` | Multi-Agent 达标启用后，符合条件请求只能进入 Coordinator/`delegate_task`；不得再调用旧抓取 Workflow |
| `_chat_with_public_job_search_fallback()` | API 内固定编排：准备 Profile → 知识库判断 → Search → 候选排序 → 页面分析 → 持久化可见候选 → 文本回答 | 汇总 `SkillRunResult`，再构造成 `ToolResult`/`ChatResult`；会计算 `jobs`、`partial_jobs`、`candidate_attempts` | 拆除其“直接网页抓取主路径”职责。兼容回答格式可由 Coordinator 合并层生成，但不能继续双轨搜索/抓取 |
| `ApplicationService.prepare/search/analyze_job_research*()` | API、Trust Smoke 和测试直接调用 `JobResearchOrchestrator` | 返回 `SkillRunResult(status, data, trace, error_code, missing_dependencies)` | 业务主路径改经 Run Service；必要兼容适配器须版本化并标记 `legacy_path_used`，不可成为默认/备用路径 |
| `JobResearchOrchestrator` in `src/starter_agent/skills/job_research.py` | 固定阶段 Workflow 直接调用 Search、Playwright 和 RAG，并在同一对象中完成网页事实与简历分析 | 状态包括 `search_profile_ready`、`waiting_for_url_selection`、`browser_failed`、`incomplete_job_description`、`resume_evidence_unavailable`、`waiting_for_jd_ingestion_confirmation` 等；data 含 `jobs`、`partial_jobs`、`candidate_attempts`、`resume_evidence`、`analysis` | 不得作为多页面默认/回退抓取器继续运行。可复用其中确定性候选排序、解析、校验和回答格式逻辑，但角色工具必须拆分并通过真实 Child Run 执行 |
| `PlaywrightJobPageReader` | `JobResearchOrchestrator.analyze_candidates()` 对每个候选执行固定 navigate → wait → 两次 snapshot 稳定性检查 | `PageReadResult(result, traces, attempts, error_code)` | 可作为 `job_web_researcher` 内部底层能力复用/演进；不得由 Router 或主 Agent直接调用 |
| `JobPageFallback` + `SafeWebFetcher` + `JobDescriptionExtractor` | Browser 失败或 JD 校验失败后自动做静态 HTTP/JSON-LD/HTML 抽取，再降级到搜索摘要 | `FallbackResult(jobs, partial_jobs, method, failures)` | 保留为受控单页能力或 Child 内部恢复手段；登录/验证码/禁止访问不得被它绕过。正常多页面路径不得回退旧 Workflow |
| `create_application()` in `bootstrap.py` | 组装单例 `AgentRuntime`、共享 Registry/Gate，并直接装配 `JobResearchOrchestrator` 和静态 fallback | `ApplicationService` 持有一个 `job_research` 编排器 | 改为组装通用 Run/Registry 服务并复用共享基础设施；每个 Run 创建独立 Context，不能复用可变 Agent 状态 |
| `run_job_research_real_smoke()` | 直接调用 Application 的 Search/Analyze 方法验证真实 Search/Playwright | 独立 Smoke Run/报告 | 新 Smoke 必须走真实 Parent/Child 路径；旧方法仅可作为明确的基线比较器，报告不能与候选方案混写 |
| `_FixtureJobResearchOrchestrator` 与现有 Trust Fixture | Fixture Runtime 直接模拟/继承固定 Orchestrator | Eval Case、Trace、Metric、Release Gate | 保留旧路径作为冻结的单 Agent baseline；新增候选路径 Fixture，使用相同 Case 和版本化输入进行比较 |
| `src/web/index.html` Chat 与 Trust 页面 | Chat 只显示流式工具事件/最终结果；Trust 可列 Eval Run 和 Trace，但取消按钮仍禁用，也没有父子树 | 前端读取 `ChatResult` 与 `/v1/trust/*` | 增加真实任务卡、恢复/取消和父子 Run 详情；不在浏览器维护权威状态 |
| `_legacy_public_job_search_answer()` | 当前源代码仅发现定义，未发现生产调用方 | 旧文本格式 | 不得重新接回默认或备用入口；若删除或保留，均需用路由测试证明没有调用证据 |

迁移约束：

1. 优先保持 `ChatResult` 和对用户可见岗位回答格式兼容；必须破坏性变更时提供明确 API/Result Envelope 版本迁移。
2. 允许提供默认关闭、限时存在的 operator 回滚开关。正常路径不得因 Child 失败自动切回旧 Workflow。
3. 每次求职调研必须记录 `route`、`legacy_path_used`、`parent_run_id`、`child_run_id` 和迁移后调用证据。
4. 当 Multi-Agent 已通过门槛并启用时，Router、主 Agent、API、前端和后台 Worker 均不得把旧 Workflow 作为默认或备用抓取入口。
5. 必须有测试证明没有重复 Search、重复页面抓取、重复计费、重复候选写入和重复 Chat 回填。
6. 若量化门槛未通过，正式系统继续使用冻结的单 Agent 基线；这不是“Multi-Agent 失败后运行时回退”，而是发布前不启用候选方案。

### 5.15 测试与评测入口

当前可复用入口：

- 全量自动测试：`uv run pytest`
- 固定求职 Fixture 基线：`agent trust fixture-baseline --run-id <stable-id>`
- 真实模型 + Playwright Smoke：`agent trust real-smoke --run-id <stable-id> --source-url <public-jd-url>`
- 现有浏览器 E2E：`pytest tests/e2e/test_playwright_job_research.py -m external -q`
- 重点现有测试目录：`tests/unit/test_job_research_*`、`tests/integration/test_job_research_*`、`tests/integration/test_rag_chat.py`、Trust/Gate/Tool exposure 相关测试。

新增验收必须复用这些入口或在同一 Eval Runner/pytest 体系内扩展；不得建立无法与现有报告、Trace 和 Safety Gate 关联的第二套测试运行器。

## 6. 非功能需求

### 6.1 安全与隐私

- 最小权限、最小上下文和 Tool Schema 不可见性是服务端强制约束，不依赖 Prompt 自觉。
- 简历、原始网页、Cookie、登录信息、验证码、Token 和隐藏日志继续遵守现有脱敏与受限 Artifact 策略。
- Child Prompt、网页文本和 Tool Observation 均视为不可信输入；不得改变 System/Policy/Task Contract。
- 所有重定向、Browser 与静态 HTTP 请求继续使用现有网络范围、SSRF、robots 和内容大小限制。

### 6.2 可靠性与一致性

- Run、状态转换、预算账本、结果版本和回填记录必须持久化并支持进程重启恢复。
- 至少一次消息投递环境下，通过幂等键、唯一约束和版本检查实现业务效果至多一次。
- Parent/Child 状态、预算与 Trace 写入应具备可审计的一致性边界；失败不得留下无归属的预算预留或孤儿 Child。
- 并发合并结果可重复：相同输入、版本和 Child Envelope 应得到相同排序、去重和冲突结果。

### 6.3 性能与资源

- 并发上限、Child 总数、页面数、重试数和 Artifact 大小必须可配置并具有安全默认值。
- 后台执行不得长期占用 Chat HTTP/SSE 连接；状态查询应为有界分页。
- 启用 Multi-Agent 后的费用和 P95 延迟必须持续满足相对基线门槛；超出时阻止发布或触发显式停用，不做静默降级。

### 6.4 可观测性

- 所有 Run 指标区分 requested/reserved/actual/remaining，并区分真实、估算和 unknown。
- 日志、Trace 和 UI 使用同一后端 ID，不以显示文本关联。
- 必须能从最终 Chat 消息追溯到 Parent、Child、来源、简历 Chunk、Policy 和模型/工具版本。

### 6.5 可维护性与兼容性

- 通用委派契约不得硬编码为只适用于悉尼或某一岗位；首版注册范围可以只包含两个 Specialist。
- 现有 Runtime/Gate/Trace 的扩展必须保持普通 Chat、知识库、邮件和现有 Tool 路径行为不变。
- Schema、Specialist、Prompt、Policy、价格表和输出契约必须版本化。

## 7. 验收标准

### 7.1 量化发布 Gate

1. 使用相同固定 Fixture、相同 provider/model、Prompt/Skill/Tool/Policy 版本和并发配置记录单 Agent 基线与候选 Run。
2. 候选方案在三项质量指标中至少一项提升 ≥10 个百分点，其余不下降。
3. Safety Gate 无新增失败；权限越权、简历幻觉、网页指令注入、敏感信息泄露均为硬阻断。
4. 候选总费用 ≤基线 1.5 倍，P95 端到端延迟 ≤基线 2 倍。
5. 真实 Search/Browser Smoke 独立记录，报告不得参与或改变固定 Gate 分数。
6. 未通过时输出“单 Agent 更优/候选未达门槛”，且生产路由不启用 Multi-Agent。

### 7.2 必验场景

| 场景 | 前置/注入 | 预期结果 |
|---|---|---|
| 双成功 | 两个 Specialist 均返回合法 Envelope | Parent `succeeded`；确定性合并 jobs 与证据；只回填一条最终消息；父子 Trace 完整 |
| 一个失败 | Web 或 Profile Child 之一失败 | 按契约 Parent `partial` 或 `failed`；成功证据保留，缺失影响显式；不得模型补齐 |
| 一个超时 | 一个 Child 超过 deadline | 该 Child `timed_out`；停止后续调用；Parent 按 failure behavior 收敛；未影响另一个 Child 的 Context/预算 |
| 父任务取消 | Parent 运行中取消 | 停止新 Child并传播取消；取消前证据保留；无合并、无最终业务回填、无取消后的新计费调用 |
| 重复回调 | 同一 Child/回填事件投递两次 | 相同 payload 幂等成功；不同 payload 报冲突；无重复消息、写入或计费记录 |
| Schema 不合法 | Child 返回缺字段/额外敏感字段 | Envelope 被拒绝或仅一次受限修复；Parent 不接收非法结果；Trace 记录校验失败 |
| 来源冲突 | 两来源对公司/地点/要求矛盾 | conflicts 中并列保留来源；不静默覆盖；投递优先级说明不把冲突当确定事实 |
| 权限拒绝 | Child 请求未分配 Tool 或 RAG scope | Tool Schema 不可见或 Gate 拒绝；无 Tool Start；Trace 关联 Policy；Child 明确失败/partial |
| 预算耗尽 | 任一五维预算到达上限 | 停止新调用；Child/Parent `budget_exhausted`；保留已完成证据；实际/预留/剩余可审计 |
| 单 Agent 更优 | 候选质量未提升、成本或延迟超限 | Release Gate 不通过；Multi-Agent 不启用；报告保留完整比较证据 |

### 7.3 补充验收

- 刷新页面、断开 SSE 或应用重启后，Parent/Child 状态可从后端恢复。
- 主 Agent 求职调研模型请求中不包含 Search/Browser/RAG 完整 Schema；两个 Child 之间也不互见未授权 Schema。
- `job_web_researcher` 能完成 Search 后跨页观察和动态页面处理，而不是只调用一次静态函数。
- 登录、验证码、权限和站点禁止访问不会触发绕过尝试。
- 原始 HTML/Snapshot 不进入 Parent messages、最终 Chat、普通日志或非受限 Trace payload。
- `profile_evidence_analyst` 的每条正向匹配均有授权 `chunk_id`，无证据时明确 missing。
- 所有 Child 使用独立 `RunContext`；并发测试证明 messages、plan、预算、取消和输出缓冲不串写。
- Legacy 开关默认关闭；启用候选方案时调用证据显示 `legacy_path_used=false`，且 Search/抓取各只执行一次预期路径。
- Trust Center 展示的树、状态、预算与失败原因和后端持久化记录一致。

## 8. 边界情况

- Search 返回空列表、重复 URL、聚合页而非详情页、过期职位或地点不符。
- 页面在两次 Snapshot 间持续变化、无限滚动、分页循环或同一职位多个跟踪 URL。
- 404 后跳首页、跨域重定向、空正文、仅脚本壳、选择器变化、页面内容过大。
- 登录/验证码出现于部分候选；其他公开候选仍可形成 partial 结果。
- 用户在 `created`、`queued`、`running`、`waiting_for_user`、合并中或回填前后重复取消。
- Worker 在 Tool 调用后、结果持久化前崩溃；恢复时不得重复业务写入，但实际外部调用应计入 usage。
- Parent 剩余某一维预算不足，而其他维预算充足；不得只检查总 Token 或总时间后继续。
- Provider 返回 usage 迟到、缺失或与估算不一致；不得将 unknown 当零。
- Registry 在 Child 运行期间停用或升级；在途 Child 使用创建时快照，新 Child 使用新状态。
- Policy 在运行中收紧；后续 Tool Call 必须按最新安全 Policy 再过 Gate，即使 Registry 快照允许。
- 两个 Child 返回相同岗位但不同公司归属、地点或要求；合并保留冲突和来源优先级依据。
- Child 成功但 Parent 已取消或进入终态；结果仅作为受限孤立/迟到 Artifact 记录，不进入合并和 Chat。
- 自动回填成功但前端未收到事件；刷新后仍只显示同一条持久化消息。
- 用户同时对同一请求重复提交；按业务幂等策略复用 Parent 或明确创建独立 Parent，不得意外共享可变 Context。

## 9. 风险与待确认事项

以下不阻塞本需求确认，但必须在设计阶段明确：

1. **后台执行基础设施**：选择数据库队列、内置 Worker 或外部队列，以及租约、心跳、重领和优雅停机的具体实现。
2. **状态一致性**：Run Store、预算账本、Artifact 和 Chat 回填跨表事务/Outbox 边界需要设计，避免“Run 成功但未回填”或重复回填。
3. **费用硬限制**：需要版本化 Provider/Model 价格来源、价格生效时间和不支持模型的保守上界策略；否则无法可靠执行费用硬预算。
4. **预算默认值**：五维 Parent/Child 默认额度、并发数、取消宽限期和可恢复重试次数需通过基线数据确定，不在需求阶段武断固定。
5. **人工处理体验**：`waiting_for_user` 的登录/验证码处理是在现有浏览器会话继续，还是返回 partial 后由用户重新发起，需要在交互设计中确定；无论哪种都不得绕过限制。
6. **Artifact 生命周期**：原始 HTML/Snapshot 的加密、访问角色、保留期、清理和容量上限需与现有隐私策略对齐。
7. **Legacy 生命周期**：回滚开关的所有者、到期时间和删除标准需在实施计划中明确，避免长期双轨。
8. **基线代表性**：固定 Fixture 需要覆盖悉尼、多站点、动态页、权限失败、冲突和简历证据缺失，防止量化门槛只对窄样本有效。
9. **现有接口兼容**：`ChatResult` 的新增 Run 字段、旧 `SkillRunResult` 适配和 JD 入库确认流程是否另行版本化，需要在 API 设计中冻结。
10. **现有工作区状态**：本次审查发现求职调研相关文件已有未提交修改；后续设计和实现必须以开始实施时的最新工作区重新核对迁移清单，避免覆盖用户改动。

## 10. 第一阶段完成定义

本文件经用户确认即完成第一阶段。确认前不进入详细设计、任务计划或代码实现；确认后下一阶段仍须先重新核对仓库差异与量化基线入口。
