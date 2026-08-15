# 求职 Agent 执行编排需求

## 文档信息

- 文档阶段：第一阶段需求稿，等待产品确认
- 审查基线：2026-08-14 当前工作区（包含未提交的委派、Trust、API、前端和测试变更）
- 目标文件：`docs/job-application-orchestration-requirements.md`
- 本阶段交付边界：仅定义需求，不实施代码、不生成实施任务、不迁移框架
- 核心约束：扩展现有执行体系，不建设第二套 Runtime、Plan/Context、Gate、Delegation、Trace 或 Eval

## 1. 需求背景

现有求职 Agent 已具备对话、固定求职 Workflow、Tool/MCP/RAG、统一工具注册和执行、`PreToolCallGate`、上下文裁剪与摘要、Token 计量、长期记忆、求职调研 Parent/Child 委派、结果 Envelope、五维委派预算、后台 Worker、Run API、Trust Trace、离线 Eval、Safety/Release Gate 和动态运行详情。但系统目前缺少一个面向所有请求、能够根据复杂度、风险、可用能力和预算选择执行方式的统一编排决策层。

当前真实仓库基线如下：

| 能力 | 当前事实 | 本需求的处理方式 |
| --- | --- | --- |
| 请求入口分类 | `knowledge/routing.py` 仅输出 `conversation`、`job_research`、`knowledge_query`；无 confidence、能力、风险或 fallback；分类失败默认进入 knowledge query | 在现有入口上扩展为统一 Execution Router；不得保留两个相互竞争的首层 Router |
| Agent Runtime | `agent/runtime.py` 中只有一个 `AgentRuntime` 和共享 Model/Tool Loop，已有模型次数、工具次数、wall-clock、Token 等约束 | 保留为唯一执行核心；Direct、Tool Loop、Plan 和 Child 均通过适配复用它，不复制 Loop |
| Workflow | `docs/workflow.md` 与求职 API 中已有固定求职流程和领域 Orchestrator | 固定、规则明确任务路由到 Workflow；纯函数、校验器和既有 Workflow 可复用，不把 Workflow 冒充 Plan |
| Context/Token/Todo | 已有 Context Builder、摘要/裁剪、Memory、Tool Result Guard；`RunContext.todo_plan` 是运行内字段，尚无通用 Plan/DAG Store | 扩展现有 Run-scoped Context 与显式执行状态；Goal、安全策略、当前 Plan、Todo 和预算状态不可被裁剪丢失；不另建 Context 系统 |
| Tool/MCP/RAG/Gate | 统一 Tool Registry、MCP Manager、RAG、`PreToolCallGate`、确认和执行器已存在 | 所有路径继续复用；Router 只决策，不能执行 Tool；任何新执行路径不得绕过 Gate |
| Multi-Agent Delegation | `delegation/` 已有真实 Parent/Child、Specialist Registry、最小 Child Context、Result Envelope、Task Manager/Dispatcher/Worker、取消、租约、有限重试、结果校验与合并 | 作为 Plan / Delegation 的第 10 阶段能力直接复用并泛化编排接入；不再实现一套 Subagent 系统 |
| 并发 | `WorkerPoolConfig.global_concurrency` 当前默认 4；支持 Specialist 并发限制；Dispatcher 默认最多 3 个 attempt | 统一由 Task Manager 和预算/限流约束；业务 Recovery 修复次数与基础设施瞬态重试分开计数 |
| 后台 Run | 已有持久化 Parent/Child 状态、事件、Run 查询/取消/恢复 API、Chat 任务卡和结果回填 | 扩展为统一后台任务契约；HTTP/SSE 不是状态真相，前端只显示后端真实状态 |
| Trace/Eval/Safety | 已有 Trust Trace、委派事件桥、Fixture Eval Runner、Safety/Release Gate 和 Trust UI | 扩展既有 Trace 事件种类；严格区分运行时 Verifier 与离线 Evaluation |
| Checkpoint | 现有委派代码已有 Coordinator/Child checkpoint 字段和恢复逻辑，这是工作区既存能力 | 本迭代不新增、不扩展、不以 Checkpoint 作为通用编排前提，也不承诺步骤级或跨重启原节点恢复；后续是否统一另行决策 |
| 前端调试 | 已有真实 Run tree、Child 状态、attempt、deadline、失败原因、租约、预算及 Merge 证据展示 | 在同一运行详情扩展 Route、Plan DAG、Join、Verify、Recovery、Model Decision；禁止静态示例状态 |

没有统一编排时，简单问题可能被不必要地规划或委派，复杂任务可能被同步串行执行，高风险动作可能只依赖 Prompt 约束，失败可能触发无边界 Reflection，且用户无法从运行详情理解“为什么走这条路”。本需求建立一层轻量、可解释、可验证、可预算、可人工接管的执行编排能力，同时保持现有稳定模块和安全边界。

## 2. 功能范围

### 2.1 范围内

1. 统一 Execution Router，选择 `direct`、`workflow`、`tool_loop`、`plan_delegation` 或 `human_review`。
2. Router、Model Router、Planner、Plan Validator、Executor、Task Manager、Join、Merge、Verifier、Recovery、Human Review 和 Stop 的契约及状态转换。
3. 前台/后台任务选择、DAG 依赖、受限并行、Parent/Child 事件驱动汇合、部分结果和取消。
4. steps、tokens、cost、wall-clock、tool_calls 五类运行时预算，以及已有 model_calls 计量的兼容保留。
5. 运行时显式执行状态、Trace 事件、动态运行详情和可解释决策。
6. 简单问答、固定周报、JD 读取/调研/排序、证据并行搜集、邮件发送前确认等求职场景。
7. 自研 Runtime、LangChain、LangGraph 与 OpenAI Agents SDK 的适用层次、迁移代价和当前选型结论。

### 2.2 范围外

1. 不实现第二套 Agent Runtime、Model/Tool Loop、Workflow Engine、Plan/Todo 系统、Context/Memory 系统、Pre-Tool-Call Gate、Delegation Runtime、Trace Store、Eval Runner 或 Safety Gate。
2. 不用 LangChain、LangGraph 或 OpenAI Agents SDK 重写已经稳定的模块；不得以学习框架为迁移动机。
3. 当前迭代不实现 LangGraph Runtime、通用 Checkpoint Store、步骤级 Checkpoint、时间旅行、从任意步骤恢复，或进程重启后在原节点继续执行。
4. 不要求 Child 共享完整主对话，不允许 Child 递归委派 Child。
5. 不允许 Router、Planner、Verifier 或 Model Router 自行执行外部写入或绕过人工确认。
6. 不改变外部系统权限模型，不因采用更大模型降低高风险动作的验证或确认要求。

### 2.3 路径定义与优先级

| 路径 | 适用条件 | 禁止行为 | 默认执行形态 |
| --- | --- | --- | --- |
| Direct | 无需 Tool 的简单解释、澄清、确认或安全拒绝 | 生成 Plan、启动 Child、调用 Tool | 前台，同轮返回 |
| Workflow | 步骤固定、规则明确、已有稳定 Workflow/Skill 可覆盖 | 临时生成开放 Plan；把确定性步骤交给多 Agent | 默认前台；达到后台阈值时可后台运行 |
| Tool Loop | 需要一个或少量外部 Tool，下一步可根据观察决定，无需预先完整计划 | 为单一读取任务创建完整 Plan/DAG；无界循环 | 默认前台，受 Tool/时间预算限制 |
| Plan / Delegation | 复杂、开放、跨来源、多阶段、存在可验证依赖或可安全并行的任务 | 未经 Plan Validator 执行；为不独立步骤强行并行 | 默认后台；小型计划可前台 |
| Human Review | 投递、发送邮件、修改外部数据、不可逆或权限敏感动作；或低置信度/缺关键输入需要用户决定 | 在确认前执行 pending action；用模型猜测用户授权 | 进入 waiting，展示动作预览和影响 |

路由优先级为：确定的高风险/不可逆动作优先 `human_review`；信息不足或低置信度先询问用户；否则再按任务复杂度与执行形态选择其余路径。Human Review 是动作前的控制状态，可由其他路径进入，不代表获批后必须重新规划整个任务。

## 3. 目标用户与使用场景

### 3.1 目标用户

- 求职者：希望 Agent 用最低必要复杂度完成问答、调研、材料分析和投递准备，并在外部动作前获得明确确认。
- 产品与研发人员：希望以统一状态和 Trace 解释每轮 Route、Plan、Child、Join、Verify、Recovery、预算和模型选择。
- 运维与安全人员：希望限制并发、费用、时长和权限，定位失败，验证没有 Gate 旁路或无限重试。
- 评测与发布负责人：希望用离线 Evaluation 比较版本、控制发布，同时不把离线评分器混入单次线上 Run 的状态决策。

### 3.2 核心使用场景

1. 用户询问简历术语或请求解释，系统走 Direct，不生成 Plan、不调用 Tool。
2. 用户要求生成固定格式求职周报，系统复用既有 Workflow，按明确步骤和 Schema 输出。
3. 用户给出一个 JD URL，系统走 Tool Loop 读取、提取、校验和引用，不启动 Multi-Agent。
4. 用户要求后台批量调研多个岗位，系统立即返回 `task_id`，后台执行并持续更新真实状态。
5. 三个互不依赖的 JD 读取任务在无共享写冲突且预算允许时并行，随后按 Join Policy 汇合。
6. JD 网页事实与简历证据可独立搜集时由两个受限 Child 并行；若简历分析依赖已标准化 JD，则必须建立依赖边而非伪并行。
7. 汇合后执行确定性去重、冲突保留和排序，再由 Verifier 检查引用、业务规则和产品 Rubric。
8. Child 超时、失败、取消或只返回部分结果时，Parent 按 Join Policy 明确保留缺失和失败，不猜测补齐。
9. 用户要求发送求职邮件，系统先准备预览并进入 Human Review；只有明确批准后才允许邮件 Tool 再次经过 Gate 执行。

## 4. 用户故事

1. 作为求职者，我希望简单问题立即回答，以免为低价值任务等待规划或多 Agent。
2. 作为求职者，我希望系统说明选择某条执行路径的原因、置信度、所需能力、风险和失败回退。
3. 作为求职者，我希望输入不足时系统提出具体问题，而不是猜测岗位、公司、邮箱、附件或投递意图。
4. 作为求职者，我希望批量调研在后台运行并立即获得 `task_id`，离开页面或 SSE 断开不会伪造状态。
5. 作为求职者，我希望看到已完成、未完成、部分结果、失败原因、预算消耗以及我可以怎样继续。
6. 作为求职者，我希望发送邮件、正式投递和修改外部记录前看到具体动作、目标、内容摘要和影响，并能批准、拒绝或修改。
7. 作为开发者，我希望 Planner 只服务复杂任务，并且每个 Step 都有完成条件、风险和预算，执行前可被确定性校验。
8. 作为开发者，我希望只有真正独立且无共享写冲突的步骤并行，所有 Child 使用最小上下文和统一输出契约。
9. 作为开发者，我希望失败恢复只修复具体失败项，最多 1–2 次，避免无限 Reflection 和全文重写。
10. 作为运维人员，我希望 Task Manager 统一控制并发、deadline、取消和有限重试，主 Agent 不通过反复模型调用轮询 Child。
11. 作为安全人员，我希望所有 Tool 继续经过现有 Gate，关闭的 Tool、缺失权限和不可逆动作在执行前被拦截。
12. 作为评测负责人，我希望线上 Verifier 只决定当前 Run 的下一状态，离线 Evaluation 才用于版本对比和发布决策。

## 5. 功能需求

### 5.1 统一 Execution Router

1. 所有用户请求在进入执行路径前必须得到一个结构化 Router Decision；安全拒绝和明显 Human Review 可由确定性规则先行，模型分类不能覆盖硬规则。
2. Router 输出至少包含：

```json
{
  "route": "direct|workflow|tool_loop|plan_delegation|human_review",
  "confidence": 0.0,
  "reason": "面向用户和 Trace 的简短依据",
  "required_capabilities": ["capability_id"],
  "risk_level": "low|medium|high|critical",
  "fallback": {
    "route": "direct|workflow|tool_loop|plan_delegation|human_review|stop",
    "condition": "触发条件",
    "user_prompt": "需要用户补充或确认时的问题"
  }
}
```

3. Router 只读取能力快照、策略、预算摘要和输入元数据，不得直接执行 Tool、创建 Child、修改外部数据或生成业务结果。
4. Router 必须综合：任务复杂度、可逆性、外部副作用、输入完整度、所需能力、能力启用/健康状态、预算、前后台偏好和已有 Workflow 覆盖度。
5. Router 必须在 Trace 中记录规则命中、候选路径、最终路径、confidence、reason、能力快照版本、风险和 fallback；不得记录隐藏推理原文。
6. confidence 低于配置阈值、关键输入缺失、候选路径冲突或风险为 high/critical 时，必须提出具体问题或进入 Human Review；不得硬猜。
7. 某 Tool/MCP/Skill/Specialist 被关闭、未安装、无权限或不健康时，Router 应选择安全 fallback、询问用户或 Stop；不得生成一个注定无法校验的计划。
8. 简单任务必须走 Direct 或已有 Workflow；不得生成 Plan，不得启动 Multi-Agent。
9. 现有 `KnowledgeRequestRouter` 必须迁移为统一 Router 的兼容分类信号或被统一入口替代，不能形成双重路由后再互相覆盖。

### 5.2 Workflow 与 Tool Loop

1. Workflow 仅用于步骤固定、规则明确、已有确定性步骤/Skill 的任务；Workflow ID、版本、输入 Schema、输出 Schema、风险点和停止条件必须可追踪。
2. 固定求职周报应以 Workflow 路径运行；若只需要本地已有数据，不得升级为 Plan / Delegation。
3. Tool Loop 复用现有 `AgentRuntime`、Tool Registry、MCP、RAG、Tool Result Guard 和 Gate；每轮只根据当前观察决定是否继续，不要求预先生成完整 Plan。
4. 单个 JD 读取默认使用 Tool Loop；只有涉及多来源开放调研、跨阶段依赖或满足委派阈值时才升级为 Plan / Delegation。
5. Tool Loop 必须受 max steps、tokens、cost、wall-clock 和 tool_calls 限制；重复相同调用、无进展或 Tool 返回明确不可重试错误时停止。
6. Tool 关闭、权限拒绝、需要确认或结果过大时，沿用既有 Gate、Confirmation、Tool Result Guard 和 Context 治理，不建立旁路。

### 5.3 Planner 与 Plan 数据契约

1. Planner 只处理 Router 已选择 `plan_delegation` 的复杂任务；Direct、Workflow、普通 Tool Loop 和单纯 Human Review 不调用 Planner。
2. Plan 必须包含稳定 `plan_id`、版本、总目标、假设、输入引用、DAG、总预算、Join Policy、全局完成条件和 fallback。
3. 每个 Step 至少包含：

```json
{
  "step_id": "stable_id",
  "goal": "该步骤唯一目标",
  "inputs": [{"ref": "artifact_or_state_ref", "required": true}],
  "capabilities": ["tool_or_specialist_capability"],
  "done_when": ["可验证完成条件"],
  "risk": "low|medium|high|critical",
  "budget": {
    "steps": 1,
    "tokens": 0,
    "cost_microunits": 0,
    "wall_clock_ms": 0,
    "tool_calls": 0
  },
  "depends_on": [],
  "execution": "local|workflow|tool_loop|child",
  "output_contract_ref": "schema_or_result_envelope_ref"
}
```

4. `inputs` 必须优先使用 Artifact/State 引用，不复制完整主对话、简历、网页原文或其他 Child 上下文。
5. `done_when` 必须可由 Schema、确定性规则或明确 Rubric 验证，不能使用“结果看起来不错”等不可判定描述。
6. Planner 生成依赖 DAG；发现必须先获得上游标准化输出的步骤必须显式连边。
7. Planner 不得自行扩大 Tool 权限、Specialist 能力、deadline 或总预算，不得把高风险动作降级为普通 Step。
8. Plan 执行前必须经过 Plan Validator；校验失败的 Plan 不得进入 Executor。

### 5.4 Plan Validator

1. Plan Validator 必须以确定性检查为主，至少检查：
   - 当前主体权限、Pre-Tool-Call Policy 与 Human Review 要求；
   - Tool、MCP Server、Skill、Workflow、Specialist 是否注册、启用、健康且版本可用；
   - 必需输入及 Artifact 引用是否存在、在授权范围内且未过期；
   - DAG 是否无环、依赖是否存在、输出契约是否能满足下游输入；
   - 每步预算之和、并行峰值和预留是否不超过 Parent/Run 总预算；
   - deadline、限流、并发上限和共享写冲突；
   - 发送、投递、修改、删除等不可逆动作是否被转换为 `pending_action` 并置于 Human Review 前；
   - Join Policy 和缺失/失败处理是否完整。
2. Validator 返回 `valid`、具体 `failures[]`、可修正字段和建议 fallback；不能只返回布尔值或笼统“计划无效”。
3. 计划循环必须在执行前失败，返回环路节点和边；不得依靠运行超时发现循环。
4. Tool 关闭、权限不足或预算不足时，不允许 Planner 反复重写整个 Plan；最多进行一次针对失败字段的计划修订，之后询问用户、Human Review 或 Stop。

### 5.5 显式执行状态

1. 每个 Run 必须维护一个版本化、可追踪的显式执行状态，至少保存：

```json
{
  "route": {},
  "plan": null,
  "current_step": null,
  "outputs": {},
  "artifact_refs": [],
  "budget": {},
  "pending_action": null,
  "revision_count": 0,
  "background_task": null,
  "child_runs": []
}
```

2. 状态更新必须带版本或等价并发控制，防止并行 Child、取消、Human Review 和回填互相覆盖。
3. `outputs` 只保存受控结构化结果或小型摘要；大结果、网页快照和敏感材料保存为受权限控制的 Artifact 引用。
4. `revision_count` 分别记录计划修订、结果 Recovery 和基础设施重试，前端不得把不同重试语义混为一个数字。
5. 显式执行状态不是步骤级 Checkpoint：本迭代允许任务失败或中断后从安全入口重新运行未完成部分，但不要求在原模型/Tool 调用点继续。

### 5.6 前台与后台任务

1. 前台任务适用于 Direct、短 Workflow、单一 Tool Loop 和能在交互时间预算内完成的小型 Plan；请求保持连接时可流式返回事件。
2. 后台任务适用于批量、多来源、长时、需要多个 Child、可能超过前台 wall-clock 或用户明确要求后台执行的任务。
3. 后台任务创建成功后必须立即返回稳定 `task_id`；可同时返回 `parent_run_id` 作为内部关联，但用户侧以 `task_id` 查询和取消。
4. 对外统一后台状态至少支持：`queued`、`running`、`waiting`、`partial`、`completed`、`failed`、`cancelled`、`interrupted`。
5. 现有内部状态可保留更细粒度语义，并进行显式映射：`waiting_children/waiting_for_user` → `waiting`，`succeeded` → `completed`，`timed_out/budget_exhausted` → `failed` 并保留 reason，Worker 停止后安全回队或无法继续时分别映射为 `queued/interrupted`。映射必须在 API 契约中固定，前端不得自行推导。
6. `partial` 表示已有可交付结果但必需项存在明确缺失；不能把仍在运行的进度称为 partial。
7. SSE/WebSocket 只加速事件更新；持久化 Run/Task Store 是状态真相。禁止用浏览器定时器或静态数据模拟状态。
8. 当前迭代不要求步骤级 Checkpoint 或跨重启在原节点恢复；重启后允许将未完成任务标记 `interrupted` 并提供安全重试入口，或复用现有委派恢复能力，但不得把后者扩展成新的通用承诺。

### 5.7 DAG 并行规则

1. 只有同时满足以下条件的 Ready Step 才能并行：
   - 所有依赖已满足，且彼此输入独立；
   - 不共享可变 Context，不依赖同一未完成输出；
   - 不存在相同外部记录、文件或业务对象的写冲突；
   - 使用统一 Result Envelope/输出契约；
   - Parent 和各 Step 预算已成功预留；
   - 全局、Specialist、Browser、Provider 和外部 Tool 限流允许；
   - 风险策略允许并发，且不包含待人工批准的副作用动作。
2. 三个独立 JD URL 的只读提取可以并行；同一份申请记录的多个写操作不得并行。
3. JD 网页事实与简历证据只有在简历检索输入不依赖标准化 JD 输出时才能并行；若需按 JD 要求匹配证据，则简历步骤依赖 JD 步骤。
4. 并行只优化 wall-clock，不得突破 tokens、cost、tool_calls 或外部速率限制。
5. 调度器必须在 Trace 中记录 `parallel_eligible` 决策及未并行原因。

### 5.8 Subagent、Task Manager 与事件

1. Subagent 并行必须复用现有 Parent Run / Child Run 模型和 Specialist Registry。
2. 每个 Child 只接收最小任务包：`goal`、必要 `inputs/artifact_refs`、允许 Tool、预算、deadline、约束、失败行为和输出契约；不得共享完整主对话、完整 Memory、完整简历或无关 Child 输出。
3. Child 必须创建独立 `RunContext`，复用同一个 `AgentRuntime` 和 Gate；Child 不得获得 `delegate_task`，不得递归委派。
4. Task Manager 复用并扩展现有 Delegation Service、Dispatcher、Worker Pool 和 Store，负责启动、全局/Specialist 并发限制、deadline、取消传播、有限基础设施重试、状态更新、事件发布和结果引用。
5. 默认全局 Child 并发以当前值 4 为兼容基线，最终值配置化；具体 Specialist/Browser/Provider 上限不得高于外部限流。
6. 基础设施瞬态失败可以有限重试；业务输出 Recovery 单独计算。任何一种默认都不得超过 2 次重试，除非已确认配置另有规定；总 attempt 仍受预算和 deadline 限制。
7. 主 Agent 不得通过重复模型调用或 Tool 调用轮询 Child 是否完成。Task Manager 依据持久化队列和结构化事件推进 Parent；前端可按事件游标订阅或读取状态。
8. Child Runtime 至少产生以下结构化事件：`child_started`、`child_progress`、`child_completed`、`child_failed`、`child_cancelled`、`child_timed_out`。
9. 每个事件至少包含 `event_id/event_seq`、`task_id`、`parent_run_id`、`child_run_id`、`status`、`occurred_at`、`attempt`、预算摘要和受控 payload/Artifact 引用；事件必须幂等、可排序、可脱敏。
10. Child 的迟到结果必须保存审计证据但标为 `late_ignored`，不得改写已完成、取消或超时的 Parent 终态。

### 5.9 Join、Merge 与部分结果

1. Parent 只在 Join Policy 满足或需要用户/系统决策时继续，不能因任意一个 Child 的 progress 事件唤醒模型。
2. 至少支持：
   - `all_required`：所有 required Child 成功或可接受 partial 后继续；任一必需结果不可用则按失败策略处理；
   - `partial_allowed`：达到最小成功数或最小字段覆盖即可继续，失败和缺失必须进入 Merge/Verify；
   - `first_success`：第一个满足输出契约且通过基本校验的结果获选，其余 Child 协作式取消或结果忽略；
   - `deadline_reached`：到达 Join deadline 时以已有合格结果进入 Merge/Verify，无结果则失败或 Human Review。
3. Join Decision 必须记录 required/optional Child、成功、partial、失败、超时、取消、缺失、被忽略和触发原因。
4. Merge 优先使用确定性合并：Schema 对齐、去重、来源保留、冲突并列、排序规则和缺失集合；模型只能处理明确需要语义判断的字段。
5. Child 失败、缺失或 partial 不得被 Parent 猜测补齐。Merge 结果必须把它们作为结构化输入交给 Verifier。
6. 汇合后岗位排序必须说明排序输入、规则/模型版本、证据覆盖和未纳入项；引用缺失的关键事实不能参与确定性高置信排序。

### 5.10 Verifier

1. 运行时 Verifier 在输出交付或副作用执行前检查当前 Run 的具体结果，至少覆盖：
   - JSON/领域 Schema；
   - 求职业务规则和 Workflow 规则；
   - 来源可信度、来源 URL/Artifact、抓取时间和字段归属；
   - 事实 Claim 与引用/证据的逐项对应；
   - 产品 Rubric，包括完整性、相关性、冲突披露、风险提示和用户要求；
   - pending action 的目标、内容、附件、权限和确认状态。
2. Verifier 返回 `passed`、`failures[]`、`verified_items[]`、`decision` 和依据。每个 failure 至少包含 `failure_id`、`scope/path`、`rule`、`expected`、`actual_summary`、`severity`、`repairable` 和 `evidence_refs`。
3. Verifier 不得只返回“质量不够”或要求模型全文重写；引用缺失必须精确到 Claim/字段。
4. Verifier 的决策仅为 `end`、`recovery`、`human_review` 或 `stop`，只影响当前 Run，不决定版本发布。
5. 高风险动作即使内容验证通过，也必须进入 Human Review；Verifier 不能代替用户授权。

### 5.11 Recovery

1. Recovery 只接收 Verifier 的 repairable failure 和相关最小上下文，按字段、Claim、引用或 Step 局部修复。
2. 每个 Run 的业务 Recovery 最多 1–2 次，默认上限待确认；超过上限必须 Stop、Human Review 或返回 partial。
3. 禁止无限 Reflection、自我批评循环、无差别重新读取所有来源或全文重写已通过部分。
4. 已通过项默认冻结；只有当修复项的依赖明确影响已通过项时，才允许把受影响项重新纳入验证，并记录原因。
5. Recovery 使用剩余预算且每次都递增 `revision_count`；预算不足时不得启动修复。
6. 不可重试错误、权限拒绝、用户拒绝、Tool 关闭、结构性计划循环和不可恢复引用缺失不进入盲目 Recovery。

### 5.12 Budget 与停止语义

1. 每个前台/后台 Run 和 Plan Step 在运行时限制：`steps`、`tokens`、`cost_microunits`、`wall_clock_ms`、`tool_calls`；现有 `model_calls` 继续保留为内部第六个保护维度或映射到 Step 消耗，不能被移除。
2. 预算必须在 Parent → Step/Child 间预留、消费、释放和结算；并行任务使用峰值预留，不能只在结束后统计。
3. 未知费用或不可审计 usage 必须按现有 fail-closed 预算策略处理，不得把 unknown 当作 0。
4. Router、Planner、Validator、Verifier、Recovery、Model Decision 和 Merge 的模型/工具消耗都计入 Run 总预算。
5. 达到任一硬限制后停止新的模型和 Tool 调用，协作式取消不再需要的 Child，保留已经完成的有效 Artifact。
6. 预算停止响应至少包含：触发维度、预算上限与已用量、已完成项、未完成项、可用部分结果、未执行 pending action，以及恢复方式（缩小范围、提高预算、重新运行未完成项或人工接管）。
7. 预算耗尽不能自动升级模型、扩大限额或绕过 Human Review。

### 5.13 Model Router

1. Model Router 根据任务复杂度、风险、结构化输出要求、上下文规模、Tool 能力、延迟目标、价格和当前预算，从已配置且可用的 Provider/Model 中选择模型。
2. 决策至少记录 `selected_provider/model`、候选、任务复杂度、所需能力、预计预算、选择理由、fallback 和定价/配置版本。
3. Direct、分类和确定性校验优先使用满足质量门槛的低成本模型或规则；复杂规划、语义合并和困难修复可按 Eval 证据使用更强模型。
4. 高风险可靠性主要依靠 Schema、业务规则、来源验证、Gate 和 Human Review，禁止用“换更大模型”代替验证或确认。
5. 模型不可用、限流或预算不足时只允许使用已配置、能力满足且通过 Eval 的 fallback；不能静默切换后省略 Trace。
6. 模型选择策略必须由离线 Evaluation 在代表性任务上比较质量、tokens、cost、latency、调用数和失败率后发布。

### 5.14 Context、Summary、Memory 与 Todo

1. 继续复用现有 Context Builder、Token Counter、Tool Result Guard、Summary/Trim、Memory 和 RunContext，不创建新的上下文管线。
2. 每次模型调用必须保留不可压缩核心：当前 Goal、系统/安全策略、用户已确认事实、当前 Route、有效 Plan/DAG、current_step、Todo、预算状态、pending action 和必要输出引用。
3. 可裁剪内容包括旧对话、冗余工具原文、重复网页和已被 Artifact/摘要替代的中间内容；裁剪必须保留来源和恢复引用。
4. Child Context 由最小任务包构建，不共享完整 Parent 对话。Merge/Verifier 只加载必要 Envelope 和证据引用。
5. Todo 用于跟踪 Run 内未完成、阻塞、waiting 和完成项；Plan 表达目标与依赖。二者可以互相引用，但不得混为一段自然语言或另建平行 Store。
6. Summary/Memory 与 Checkpoint 的概念边界：
   - Summary 是为模型压缩语义上下文，不保证恢复精确执行位置；
   - Memory 保存跨会话、经过治理的用户事实或偏好，不保存执行栈；
   - Todo/Plan 保存显式任务意图、依赖和状态；
   - Checkpoint 保存某 Runtime/Graph 在特定执行点的可恢复状态快照。

### 5.15 Human Review 与不可逆动作

1. 以下动作至少为 high risk：正式投递、发送邮件/私信、修改外部申请状态、创建/更新/删除外部数据、采用最终材料覆盖原版本、涉及敏感信息或未经证实经历的写入。
2. 进入 Human Review 时生成 `pending_action`，至少包含动作类型、目标系统/收件人、内容摘要或 Diff、附件/Artifact、预期副作用、可逆性、风险、Gate 决策、失效时间和确认 ID。
3. 用户可以批准、拒绝或要求修改。批准必须绑定精确 action hash、主体和有效期；内容、收件人、附件或目标改变后必须重新确认。
4. 获批动作在真正执行前仍需再次经过现有 `PreToolCallGate` 和能力快照检查，确认不代表永久授权或权限提升。
5. 用户拒绝后不得通过 Recovery 重建同一动作并再次执行；只可返回草稿或等待新指令。
6. 低置信度和输入缺失造成的 waiting 可使用同一前端交互模式，但必须与“高风险动作审批”在状态和文案上区分。

### 5.16 Trace、运行详情与前端调试

1. 每轮 Trace 在现有 Trust Trace/Run Event 体系中记录：Route、Plan、Plan Validation、Parent Run、Child Run、Task Event、Join Decision、Merge、Verify、Recovery、Budget、Model Decision 和 Human Review。
2. 事件必须关联可获得的 `session_id`、`turn_id`、`task_id`、`parent_run_id`、`child_run_id`、`step_id`、`model_request_id`、`tool_call_id`、`policy_decision_id`、`approval_id`、`plan_id/version` 和父事件。
3. Trace 保存结构化决定、原因码、摘要和引用，不保存隐藏推理、明文密钥、未脱敏简历全文、邮件正文或网页快照。
4. 现有运行详情必须从真实 API/事件流展示：
   - Router 的 route、confidence、reason、能力、风险和 fallback；
   - Plan Step、依赖 DAG、校验结果、current step 和并行资格；
   - 前台/后台形态、task_id 和统一状态；
   - Parent/Child、attempt、deadline、取消和事件时间线；
   - Join Policy、Join Decision、缺失/失败/partial 如何进入 Merge；
   - Verifier 失败项、Recovery 次数和最终决策；
   - 各层预算 limit/reserved/consumed/remaining；
   - Model Decision 和配置/定价版本。
5. 前端不得展示静态或推导出来的“假状态”；刷新、重连和跨页面后必须从 Run API 恢复。事件按 cursor/version 去重。
6. 运行详情不展示 Chain-of-Thought；可解释性由结构化 reason、规则命中、输入引用和决策记录提供。

### 5.17 运行时 Verifier 与离线 Evaluation

| 维度 | 运行时 Verifier | 离线 Evaluation |
| --- | --- | --- |
| 对象 | 当前单个 Run/Step/Merge 输出 | 固定数据集上的版本、模型、Prompt、Router、Policy 或系统变更 |
| 时机 | 线上执行中 | 发布前、回归、定期评测或人工触发 |
| 输入 | 当前结果、Schema、规则、证据、Rubric | Fixture/历史脱敏案例、基线版本、候选版本、Judge/规则 |
| 输出 | 具体 failures 和 `end/recovery/human_review/stop` | 指标、差异、失败簇、回归结论和 Release/Safety Gate 输入 |
| 禁止事项 | 决定系统版本发布；修改全局策略 | 直接驱动当前业务 Run 的 Tool 或外部动作 |

离线 Evaluation 必须覆盖各路由准确率、低置信度校准、Plan 有效性、并行收益、引用正确率、Recovery 成功率、预算遵守、Human Review 拦截率、质量、延迟和成本。发布决策继续使用现有 Eval Runner 与 Safety/Release Gate，不新增线上 Judge 服务冒充发布门。

### 5.18 框架选型与迁移边界

| 方案 | 适用层次 | 对当前仓库的价值 | 迁移代价/冲突 | 当前结论 |
| --- | --- | --- | --- | --- |
| 现有自研 Runtime | 现有 Model/Tool Loop、Gate、Context、Budget、Parent/Child、Run Store、Trace 和产品状态 | 与现有领域能力、审批、安全、UI 和 1300+ 测试基线直接一致 | 需要补齐统一 Router、Planner/DAG、Join、Verifier/Recovery 和通用状态契约 | **本迭代继续采用**，增量扩展现有抽象 |
| LangChain | 快速接入模型/Tool、标准 Agent 和 Middleware；官方 Agent 支持 Tool 序列/并行、重试、状态与 Middleware | 可作为未来 Provider/Tool Adapter 或原型对照 | 当前 LangChain Agent 本身运行在 LangGraph 上；整体引入会与现有 Runtime、Context、Gate、Todo、HITL 和 Trace 重叠，迁移与回归成本高 | **不迁移 Runtime**；仅在独立适配价值经 Eval 证明时局部采用。参考[官方 Agents 文档](https://docs.langchain.com/oss/python/langchain/agents) |
| LangGraph | 复杂确定性/Agent 图、持久化 Checkpoint、Interrupt、故障恢复和时间旅行 | 当未来明确需要跨重启原节点恢复、长时间人工中断、任意节点续跑或图级可视化时有价值 | 需要把当前状态机、Run Store、Worker、审批恢复和 Trace 映射为 Graph/Thread/Checkpointer；容易形成第二 Runtime | **本迭代不采用**。Checkpoint 保存线程图状态；Interrupt 依赖 Checkpointer 暂停并恢复，见[Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)与[Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) |
| OpenAI Agents SDK | OpenAI 模型上的 Agent Loop、Tool、Handoff/agent-as-tool、Guardrail、会话和 Trace 编排 | 可作为未来 OpenAI 专用 Adapter、对照实验或新隔离服务的候选 | 整体替换会重复现有 AgentRuntime、Provider 多样性、Gate、Parent/Child、审批、预算和 Trust Trace，并引入厂商特定状态语义 | **本迭代不迁移**；只有代表性 Eval 显示显著净收益且能保持现有 Gate/Trace/预算契约时再做 RFC。参考 OpenAI 官方的[编排与 Handoff 文档](https://developers.openai.com/api/docs/guides/agents/orchestration)；模型编排仍应明确 Tool、Schema、并发、重试和停止条件，见[Model guidance](https://developers.openai.com/api/docs/guides/latest-model) |

框架决策必须以需求覆盖、迁移矩阵、回归风险、可观测性、供应商锁定、性能、成本和 Eval 结果为依据。允许结论为“不迁移”，不得为学习框架重写稳定模块。

Checkpoint/Interrupt 的未来采用条件至少包括：确有跨进程/跨重启从原节点恢复的业务需求；等待人工审批可能持续数小时或数天且不能安全重跑；任务副作用已具备严格幂等；现有 Run Store/状态机维护成本高于迁移；完成数据迁移、Trace/Gate/预算映射和全量回归。当前显式执行状态、Summary、Memory、Todo 或后台 task_id 均不等同于 Checkpoint。

## 6. 非功能需求

### 6.1 安全与权限

1. 所有 Tool/MCP/RAG/外部写入继续经过现有 Registry、Policy、`PreToolCallGate`、Confirmation/Approval 和 Safety Gate。
2. Router/Planner 输出是不可信候选，不可直接授予能力；有效能力来自运行时快照和 Gate。
3. Child 最小权限、最小上下文，禁止权限继承扩大和递归委派。
4. 高风险、不可逆、敏感数据和外部写入默认 fail-closed。
5. Trace、Artifact、前端和日志按现有脱敏与访问控制治理。

### 6.2 可靠性与一致性

1. Run/Task/Event/Result/Approval 更新必须幂等并支持乐观版本控制或等价机制。
2. Parent、Child、attempt、回填、取消和迟到结果不能产生双重终态或重复外部动作。
3. 结构化事件可重放但不得重复推进业务状态。
4. 无 Checkpoint 时，中断必须显式呈现，重试从安全边界开始并复用已验证 Artifact，不能声称原地恢复。

### 6.3 性能与资源

1. Direct 路径不得因 Planner、Verifier 模型调用或 Run tree 创建增加明显额外延迟。
2. 并行只在独立性和预算校验通过后启用；默认全局 Child 并发保持配置化且不高于兼容基线 4，待确认后冻结。
3. 后台任务创建接口应快速返回，不等待 Child 完成；状态查询分页，事件使用游标。
4. 原始长 Tool 结果不进入 Parent Context；使用摘要和 Artifact 引用控制 Token。

### 6.4 可解释性与可观测性

1. 每个自动决策提供结构化原因码和用户可读摘要，不依赖隐藏推理。
2. 从 Chat Turn 可追踪到 Route、Plan、Step、Parent/Child、Tool/Gate、Artifact、Verify、Recovery、Budget 和 Model Decision。
3. 预算、失败项、缺失、冲突、部分结果和停止原因必须可查询且与前端一致。

### 6.5 兼容与可演进性

1. 普通 Chat、现有 Workflow、单 Tool、MCP、RAG、邮件审批、求职调研委派和 Trust Eval 不得回归。
2. 旧 route/status/API 字段需要兼容映射和版本策略；不能在同一版本静默改变语义。
3. Planner、Verifier、Model Router 和 Join Policy 以可替换策略接口接入现有 Runtime，不绑定某单一模型厂商或外部框架。

### 6.6 可测试性

1. Router、Plan Validator、DAG 并行判定、Join、Verifier、Recovery、Budget 和状态映射必须能用固定 Fixture 确定性测试。
2. 外部模型/网页/邮件不可用时使用现有 Mock/Fixture，不把网络波动混入核心验收。
3. 离线 Eval 与运行时测试分别收集，任何单次线上 Verifier 通过都不能替代发布回归。

## 7. 验收标准

### 7.1 总体验收

1. 一个请求只产生一个首层 Execution Router Decision，并具有全部必需字段；Router 阶段没有 Tool/Child/外部写入。
2. 简单任务无 Plan、无 Child、无多余 Tool；复杂任务必须先有通过校验的 Plan。
3. 每个 Tool Call 都能关联现有 Gate/Policy/Approval；关闭 Tool 和高风险动作不可旁路。
4. 每个后台任务创建后立即返回 task_id，刷新页面后状态、预算和 Child 信息仍来自真实后端。
5. 任一预算维度耗尽都停止新工作，返回完成/未完成/恢复方式。
6. 每个 Verifier failure 可定位，Recovery 不超过配置上限且不全文重写无关内容。
7. 前端运行详情能从真实事件展示完整编排链，不包含静态占位或隐藏推理。
8. 现有 Agent Runtime、Context、Gate、Delegation、Eval、Trace 和 Safety Gate 只有一套权威实现。

### 7.2 固定场景验收矩阵

| 场景 | 预期路径/行为 | 必验结果 |
| --- | --- | --- |
| 简单问答 | Direct | 无 Plan、无 Tool、无 Child；Trace 有 route 和 model decision（若调用模型） |
| 固定求职周报 | Workflow | 命中固定 Workflow/版本和 Schema；不启动 Multi-Agent |
| 读取单个 JD | Tool Loop | 只读 Tool 经 Gate；有来源和字段校验；默认无 Plan/Child |
| 后台批量调研 | Plan / Delegation + background | 立即返回 task_id；状态可查询/取消；HTTP/SSE 断开不改变真相 |
| 三个独立 JD 并行读取 | Plan / Delegation | DAG 三个 Ready Step 可并行；均用 Result Envelope；受并发和预算限制 |
| JD 与简历证据并行搜集 | Plan / Delegation | 输入独立时并行；若简历匹配依赖标准化 JD，则 DAG 强制串行 |
| 汇合后排序 | Join + Merge + Verify | 排序只使用已校验输入；显示缺失、冲突、引用和排序依据 |
| Child 超时 | Join Policy | 产生 child_timed_out；按 all_required/partial_allowed 等策略处理，不猜测 |
| Child 失败 | Join Policy | failure 和 error code 进入 Merge/Verify；有限基础设施重试 |
| Child 取消 | Cancel | 产生 child_cancelled；Parent 状态和其他 Child 处理符合策略 |
| 部分结果 | partial_allowed 或 deadline_reached | 明确成功项、缺失项、失败项和可信度；不得声称完整 |
| 发送求职邮件 | Human Review | 展示收件人、正文摘要/预览、附件和副作用；明确批准且 Gate 再校验后才发送 |
| 低置信度 | Ask/Human Review | 不执行 Tool、不生成武断结论；提出具体澄清问题并记录 fallback |
| Tool 关闭 | Fallback/Stop | Plan Validator 或 Router 明确失败能力；不调用、不伪造结果 |
| 计划循环 | Plan Validation failed | 返回具体环路节点/边；零 Step/Child 执行 |
| 引用缺失 | Verify failed | 指向缺失 Claim/字段；仅修复相关引用，最多 1–2 次 |
| 预算耗尽 | Stop/partial | 停止新调用；返回触发维度、已完成、未完成、部分结果和恢复方式 |
| 不可逆外部数据修改 | Human Review | 精确 action hash 审批；修改 action 后旧审批失效 |
| Parent deadline 到达 | deadline_reached | 已有合格结果进入 Merge/Verify；无结果则 failed/stop，不无限等待 |
| first_success | Join | 首个通过契约的结果获选；其他 Child 取消或 late_ignored，不能改写终态 |
| 前台超时转后台 | 需按已确认策略 | 不重复执行已完成 Tool；返回 task_id 或明确停止，不静默丢失 |

### 7.3 Trace 与 UI 验收

1. 对任一 Plan / Delegation Run，可从一个 task_id 查看 Route → Plan → Validation → Step/Child → Task Event → Join → Merge → Verify → Recovery/Human Review/END 的关联时间线。
2. DAG 可视化或等价依赖列表反映真实依赖和并行资格；不是模型输出的静态 Markdown。
3. UI 展示后台统一状态、内部原因、预算 limit/reserved/consumed/remaining、失败项和修复次数。
4. 刷新和事件重连不会丢状态或重复展示完成；取消、审批和恢复操作使用真实 API 和版本号。

### 7.4 回归与发布验收

1. 现有普通 Chat、Workflow、Tool/MCP/RAG、邮件确认、Delegation、Trust、Eval 和前端关键测试通过。
2. 静态检查证明没有第二个 Agent Loop、Gate、Run Store、Trace Store 或 Eval Runner。
3. 离线 Evaluation 比较新旧 Router 在固定案例上的准确率、校准、质量、延迟、tokens、cost、tool_calls、重试和 Human Review 命中。
4. Safety/Release Gate 通过后才允许默认启用；单次 Smoke 或运行时 Verifier 通过不等于可发布。

## 8. 边界情况

1. Router 输出非法 Schema：结构化重试一次；仍失败则安全 fallback/询问用户，不能默认执行高能力路径。
2. Router 高 confidence 但硬风险规则命中：硬规则优先进入 Human Review。
3. 用户同时要求“只解释”和“帮我发送”：拆分为可 Direct 的解释与待 Human Review 的外部动作，不扩大授权。
4. 输入包含多个任务：若相互独立且低复杂度，可拆成多个前台结果；若存在依赖或共享预算，则生成一个 DAG。
5. Tool 在路由后、执行前被关闭：Plan Validator 或 Pre-Tool-Call Gate 再检查并停止/回退。
6. MCP 健康快照过期：执行前刷新或 fail-closed；不得使用过期启用状态推断可用。
7. DAG 自环、间接环、缺失节点或下游输入无法由上游输出满足：校验失败并列出具体路径。
8. 两个并行 Step 写同一外部申请记录：标记写冲突并串行；写动作仍逐项 Human Review。
9. Provider 限流：只按已配置 fallback 和剩余预算有限重试；不得造成多 Child 重试风暴。
10. Child progress 频繁：事件可合并/限速；不得每条 progress 唤醒 Parent 模型。
11. Child 完成后 Envelope Schema 不合法：允许一次局部结构修复；仍失败则 rejected，不把原始输出交给 Parent 猜测。
12. Child 已取消后返回结果：记录 late_ignored，不合并、不回填、不改变终态。
13. all_required 中可选 Child 失败：不阻止 Join，但失败进入 Verify 和用户说明。
14. partial_allowed 未达到最低成功数：不得输出 partial 冒充可交付结果，进入 failed/stop/human review。
15. first_success 的首个完成结果未通过 Schema/来源校验：不能获选，继续等待下一个合格结果。
16. deadline_reached 时只有未经验证结果：先 Verify；无法通过则失败，不以截止时间为由降低质量门槛。
17. 引用 URL 存在但不能支持 Claim：按引用缺失处理，不以“有 URL”视为验证通过。
18. Budget 在并行预留阶段不足：减少并行度、缩小计划或询问用户；不能先启动再超额。
19. cost usage unknown：按 fail-closed 处理并提示计价配置，不记为零成本。
20. 用户在 Human Review 等待期间修改内容：旧 action hash 和批准失效，生成新 pending action。
21. 用户取消 Parent：停止创建新 Child，向运行中 Child 传播取消；保留审计证据，不执行 pending action。
22. 应用重启且通用 Run 无步骤 Checkpoint：状态标为 interrupted 或从安全边界重试；不得声称原节点续跑。
23. Summary 触发时：Goal、安全策略、当前 Plan/Todo、预算和 pending action 必须保留。
24. 前端断线/重连：按 event_seq/version 去重，并重新读取权威状态。
25. 离线 Eval 服务不可用：不影响当前运行时 Verifier 的确定性安全判断，但阻止候选版本发布。

## 9. 风险与待确认事项

### 9.1 已识别风险

1. **双 Router 风险**：现有知识/求职分类与新 Execution Router 若并存，会出现路径覆盖、重复模型调用和无法解释的 fallback；实施时必须确定唯一首层入口和兼容迁移。
2. **Plan/Todo/Checkpoint 概念混淆**：当前 `todo_plan` 是 RunContext 字段，委派又已有 checkpoint。新增编排状态若另建 Store 或把 Summary 当恢复点，会形成第二套状态真相。
3. **状态词不一致**：现有内部 `succeeded/timed_out/budget_exhausted/waiting_children` 与目标后台状态不同，必须在 API 层固定映射并保留 reason。
4. **重试叠加**：Router 结构化重试、Planner 修订、Tool 重试、Worker attempt 和 Recovery 若分别无总预算，会放大成本和副作用风险。
5. **并行错误**：错误判断输入独立或忽视共享写冲突会造成不一致、重复外部调用或错误排序。
6. **模型路由伪安全**：把高风险请求交给更强模型不能替代 Gate、Schema、来源验证和人工批准。
7. **前端泄露**：运行详情信息增多，若直接显示 Planner/Child 原文，可能泄露简历、邮件、网页或隐藏推理；只能显示结构化摘要和授权 Artifact。
8. **现有委派过度专用**：当前 Parent `run_type` 和入口偏向岗位调研，泛化时容易复制新的 Task Manager；应扩展兼容枚举/策略，不复制 Store/Worker。
9. **框架迁移回归**：LangChain/LangGraph/Agents SDK 均与现有 Loop、Gate、状态和 Trace 有重叠；无量化收益的迁移会显著增加回归面。

### 9.2 最多 5 个必要确认问题

1. **统一入口与置信度**：是否确认新 Execution Router 成为 `/v1/chat` 唯一首层路由，现有 `KnowledgeRequestRouter` 只作为特征/兼容适配；首版低置信度阈值是否采用可配置默认值 `0.70`，高风险规则始终覆盖模型 confidence？
2. **Plan/Todo 与前后台边界**：是否确认 Plan/DAG 和 Todo 只作为现有 Run 的显式持久状态扩展，而不新增通用 Checkpoint；默认“预计超过 10 秒、两个及以上 Child、批量 3 项以上或用户明确要求”进入后台，其余保持前台？
3. **并发、Join 与重试默认值**：是否沿用全局 Child 并发 4，并为 Browser/Specialist 单独配置上限；业务 Recovery 默认 1 次、最多 2 次，基础设施瞬态重试默认 2 次；默认 Join 为 `all_required`，只有明确标记时使用 `partial_allowed/first_success/deadline_reached`？
4. **预算与 Model Router 策略**：首版各路径的 steps/tokens/cost/wall-clock/tool_calls 默认额度、用户是否可临时提高额度，以及可用模型候选/价格表由哪份配置和哪组离线 Eval 作为发布依据？
5. **人工确认与调试可见性**：是否确认复用现有 Tool/邮件 Approval 作为唯一审批 Broker，并请确定 pending action 的批准有效期、可审批角色，以及运行详情中 Planner/Child 输入与 Artifact 对普通用户、开发者、管理员分别可见到什么脱敏级别？

在上述问题确认前，可以冻结契约和测试 Fixture，但不应锁定阈值、默认并发、默认预算、路由迁移开关或前端权限细节。
