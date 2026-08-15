# Agent Runtime 框架选型决策

## 文档信息

- 类型：Architecture Decision Record（ADR）
- 状态：Proposed，等待设计确认
- 日期：2026-08-14
- 关联设计：`docs/job-application-orchestration-design.md`
- 决策范围：求职 Agent 执行编排的 Runtime、状态图、持久化、人工确认、多 Agent、Trace 与框架边界

## 1. 决策摘要

本迭代选择：**不迁移 Agent Runtime。保留并扩展现有自研 `AgentRuntime + RunContext + SQLiteRunStore + Delegation + Gate + Trust Trace`，用代码实现显式 State/Node/Conditional Edge 编排。**

LangChain、LangGraph 与 OpenAI Agents SDK 作为能力参考和未来 Adapter/Spike 候选，不进入当前生产 Runtime：

- LangChain 可用于未来模型/Tool/Middleware 局部适配或原型，但不接管 Agent Loop、Context、HITL、Todo 或 Tool Governance。
- LangGraph 的 State/Node/Edge 思路用于设计表达；其 Runtime、Checkpoint、Interrupt 当前不实现。
- OpenAI Agents SDK 可用于未来 OpenAI 专用隔离 Spike，但不接管现有多 Provider Loop、Gate、Parent/Child、Budget 或 Trace。

任何未来框架建议都必须先通过最小 Adapter 和同一固定 Fixture，证明权限、预算、事件、状态、引用、人工确认和现有测试契约没有丢失；不得直接全面重写。

## 2. 决策背景

### 2.1 当前仓库已经拥有的能力

仓库不是空白 Agent 原型，已经形成以下生产边界：

1. `agent/runtime.py::AgentRuntime`：唯一 Model/Tool Loop，支持 tool-free turn、结构化 Tool 调用、取消、重复调用治理、Token/模型/Tool/wall-clock 限制。
2. `delegation/context.py::RunContext`：消息、Tool View、working memory、Todo、Summary/Trim、Artifact refs、预算、取消和 Trace context 的 Run-scoped 隔离。
3. `capabilities/gate.py`：Tool Registry、Policy、`PreToolCallGate`、Permit、Confirmation 和 `UnifiedToolExecutor` 的统一强制路径。
4. `delegation/`：持久 Parent/Child、Specialist Registry、最小 Child Context、Dispatcher/Worker、租约/心跳、取消、有限重试、Result Envelope、Validator、Merger 和 Chat backfill。
5. `delegation/budget.py`：Parent/Child 预算预留、结算、释放以及 unknown cost fail-closed。
6. `SQLiteRunStore`、Session Store、Artifact、Outbox：状态、事件、结果和回填的权威持久化。
7. Trust Trace、Eval Runner、Safety/Release Gate 与动态前端详情。
8. 大量单元、集成、E2E、Fixture 和安全回归测试已经绑定这些契约。

因此，框架比较的基准不是“哪个框架功能最多”，而是“哪个方案能以最低风险补齐 Router、Planner/DAG、Join、Verifier/Recovery 和通用状态图，同时不复制现有权威边界”。

### 2.2 决策驱动因素

- 对 Runtime、Tool、权限、预算和状态转移的控制力；
- 对现有模块的复用程度；
- 状态持久化、后台任务、Parent/Child 和事件幂等能力；
- 人工确认与不可逆动作的 fail-closed 语义；
- 可观测性、脱敏、审计关联和离线 Eval；
- 多 Provider 与供应商绑定；
- 迁移成本、测试回归面、可回滚性；
- Checkpoint/Interrupt 的当前必要性，而非理论吸引力。

## 3. 候选方案

### 3.1 自研 Runtime

#### 价值

- 对 Model/Tool Loop、Tool exposure、Gate、预算、上下文和失败语义有完全控制。
- 当前所有核心模块已经接入，复用程度最高。
- Parent/Child、Run Store、Artifact、Trace 和前端状态已经按求职场景落地。
- Provider 抽象支持 OpenAI-compatible、Mock 和其他已配置 Provider，不锁定单一厂商。
- 可用增量 State/Node/Edge Controller 补齐编排，无需重写执行面。

#### 成本

- 需要自己维护 Router、Planner Schema、DAG Scheduler、Join Policy、Verifier 插件和状态迁移。
- 状态机、并发竞态、事件幂等和版本迁移必须由项目测试保证。
- 缺少现成图可视化、通用持久图调试和生态 Middleware。

#### 边界

保留自研不等于继续把逻辑堆进 API。必须提取清晰的 orchestration package、结构化模型、条件 Edge、Store CAS 和测试 Fixture，避免 Controller 变成新的巨型函数。

### 3.2 LangChain

LangChain 的高层 Agent API 提供模型/Tool 集成、顺序或并行 Tool 调用、动态 Tool 选择、重试、状态以及 Middleware。Middleware 可用于模型选择、Context 处理、Tool 错误、限流、Guardrail、HITL、Todo 和观测。参考[LangChain Agents 官方文档](https://docs.langchain.com/oss/python/langchain/agents)与[Middleware 官方文档](https://docs.langchain.com/oss/python/langchain/middleware/overview)。

#### 价值

- 快速接入不同模型、Tool 和社区 Integration。
- 高层 `create_agent` 对标准 Agent Loop 和 Middleware 扩展较友好。
- 可作为未来 Provider/Tool Adapter、实验 harness 或非核心原型层。
- Middleware 模式可为现有 Model Router、Summary、限流与日志设计提供参考。

#### 边界与冲突

- 当前 LangChain Agent 的执行和 Middleware 与现有 AgentRuntime、Tool Loop、Context、Todo、重试和 Gate 高度重叠。
- LangChain Agent 基于 LangGraph 运行；采用高层 API仍会引入新的状态/运行语义，不只是增加几个 Tool wrapper。
- 社区 Tool 或 Middleware 不能自动满足现有 `PreToolCallGate`、Permit、Approval hash、Artifact access、Parent/Child Budget 和 Trust Trace 契约。
- 若让 LangChain 执行 Tool，就会出现两个 Tool Executor 或需要在所有入口额外桥接 Gate，旁路风险高。

#### 适用结论

当前不用于主 Runtime。允许未来只在以下局部使用：

- 将某个 Provider/Tool 适配到现有接口，且最终执行仍经过现有 Gate；
- 构建离线对照实验，不写生产 Run Store；
- 独立、低风险、无外部写入的 Spike。

### 3.3 LangGraph

LangGraph 强项是显式 State、Node、Conditional Edge、持久化、长期运行和 HITL。其 Persistence 使用 Checkpointer 保存线程图状态；Interrupt 暂停节点并依赖持久状态恢复。参考[Persistence 官方文档](https://docs.langchain.com/oss/python/langgraph/persistence)与[Interrupts 官方文档](https://docs.langchain.com/oss/python/langgraph/interrupts)。

#### 价值

- State/Node/Edge 与本需求的 Router、Planner、Executor、Join、Verifier、Recovery 非常契合。
- 图结构适合表达条件分支、fan-out/fan-in、循环上限和终止条件。
- Checkpoint、Interrupt、状态历史、故障恢复和时间旅行适合真正长期、可恢复的任务。
- 生态可提供图级调试与可视化。

#### 边界与冲突

- 当前仓库已有 Parent/Child 状态机、SQLite Run Store、租约 Worker、Approval resume、Context checkpoint 字段、事件和 Trace。
- 引入 LangGraph Runtime 必须决定 Graph State 与 ParentRun/RunContext 谁是权威；双写会产生一致性和恢复语义冲突。
- Checkpoint replay 可能重新执行节点；所有外部副作用必须先满足严格幂等，当前迭代没有此迁移目标。
- Interrupt 的暂停/恢复语义会与现有 Confirmation Broker、waiting_for_user 和 Run resume API 重叠。
- 把现有 Tool Gate、预算、Artifact、Trace、Worker/lease 映射为 LangGraph 节点需要大范围迁移和回归。

#### 当前结论

只采用其**显式 State/Node/Edge 设计模式**，不采用 LangGraph Runtime。Checkpoint/Interrupt 只记录未来采用条件，不作为当前实现、任务或验收门禁。

### 3.4 OpenAI Agents SDK

OpenAI Agents SDK 提供 Agent Loop、Tools、Handoffs、agents-as-tools、Guardrails、Sessions、结果/状态与 Tracing。官方编排文档区分：Handoff 让 Specialist 接管对话；agents-as-tools 让 manager 保持最终答复所有权。参考[OpenAI 官方 Orchestration and handoffs](https://developers.openai.com/api/docs/guides/agents/orchestration)。

#### 价值

- 对 OpenAI 模型有直接、轻量的 Agent/Tool/Guardrail/Tracing 集成。
- Handoff 与 manager-style agents-as-tools 提供两种清晰协作模式。
- Sessions、结果状态和 Trace 能降低新项目搭建成本。
- 可作为 OpenAI 专用执行 Adapter 或多 Agent模式的对照实验。

#### 边界与冲突

- SDK 自带 Agent Loop，与唯一 `AgentRuntime` 重叠。
- Handoff/agent-as-tool 语义不等于现有持久 Parent/Child、Task Contract、最小 Context、Budget Ledger、lease/heartbeat 和 Result Envelope。
- Guardrails 不能自动替代现有 `PreToolCallGate`、网络策略、Approval hash 和 Unified Tool Executor。
- Sessions/Tracing 若直接启用，会形成第二状态/Trace 来源，需要明确投影与数据治理。
- 更偏 OpenAI 生态；仓库现有 Provider 抽象与本地/兼容 Provider 会受到供应商绑定影响。
- SDK 的当前能力可能随版本变化，不能把未由本地配置和测试证明的模型或特性硬编码进产品设计。

#### 当前结论

不迁移主 Runtime。若未来需要 Spike，应采用“SDK Runner 在 Adapter 后、禁止直接执行生产 Tool”的方式：先只跑无副作用固定 Fixture，结果转换为现有 Result Envelope，并写入现有 Trace adapter；通过边界测试后才讨论更深集成。

## 4. 多维比较

评分：5 表示最符合当前仓库，1 表示需要重大补偿。评分用于相对决策，不代表框架通用质量。

| 维度 | 自研 Runtime | LangChain | LangGraph | OpenAI Agents SDK |
| --- | ---: | ---: | ---: | ---: |
| 当前代码复用 | 5 | 2 | 2 | 2 |
| Tool/Gate 控制力 | 5 | 2 | 3 | 2 |
| 现有状态持久化兼容 | 5 | 2 | 2 | 2 |
| Parent/Child/后台 Worker 兼容 | 5 | 2 | 3 | 2 |
| 人工确认兼容 | 5 | 2 | 3 | 2 |
| 现有预算账本兼容 | 5 | 2 | 2 | 2 |
| 现有 Trace/Eval 兼容 | 5 | 2 | 2 | 2 |
| 显式图表达 | 3 | 3 | 5 | 3 |
| Checkpoint/Interrupt 能力 | 2 | 4 | 5 | 3 |
| 多 Provider/低绑定 | 5 | 5 | 5 | 2 |
| 当前迁移成本 | 5 | 2 | 1 | 2 |
| 当前回滚容易度 | 5 | 3 | 2 | 3 |

### 4.1 状态持久化

- **自研**：现有 `SQLiteRunStore` 是业务真相，已有 version/CAS、事件、Artifact、Merge、Outbox、lease 和恢复规则；增量字段风险最低。
- **LangChain**：高层 Agent State 适合单 Agent，但要与现有业务 Parent/Child Store 对齐仍需自定义桥接。
- **LangGraph**：Checkpoint 很强，但若引入会与现有 Run Store 形成权威选择和数据迁移问题。
- **Agents SDK**：Sessions/Run State 对 SDK 执行有价值，但不能直接替代现有业务任务、预算和 Worker 状态。

### 4.2 人工确认

- **自研**：现有 Confirmation/Email Approval 已与 Gate、Permit、action hash 和 UI 结合。
- **LangChain/LangGraph**：HITL/Interrupt 有通用性，但需映射当前 Approval identity、expiry、policy revision 和 exactly-once Tool execution。
- **Agents SDK**：Guardrails/HITL 可表达暂停，但仍需接入现有 Approval Gate；否则会有第二审批系统。

### 4.3 可观测性

- **自研**：现有 Run Event + Trust Trace 已关联 Session/Turn/Model/Tool/Policy/Approval/Parent/Child，且符合本地脱敏与 UI。
- **LangChain/LangGraph**：可结合 LangSmith 获得图和运行调试，但会增加外部观测后端和数据治理，需要双 Trace 关联。
- **Agents SDK**：内置 Tracing 对 SDK 流程有价值，但与 Trust Store 双写、敏感数据和非 OpenAI Provider 需要适配。

### 4.4 供应商绑定

- 自研 Provider Registry 和 LangChain/LangGraph 均可支持多个 Provider；自研当前已落地。
- Agents SDK 对 OpenAI 生态集成最佳，但主 Runtime 采用会提高模型、Trace、Session 与工具语义绑定。
- 供应商绑定本身不是绝对否决项；只有质量、成本、延迟和维护收益经同一 Eval 显著优于现状时才可接受。

### 4.5 迁移成本与测试契约

现有测试验证的不只是最终文本，还包括 Tool View、Gate 不旁路、RunContext 隔离、Parent/Child 状态、预算、事件、取消、Result authority、Trace 和 UI。任何完整框架迁移都需要逐项重建这些契约。保留自研方案只新增 Router/Plan/Join/Verifier 等测试，回归面更可控。

## 5. 最终选择与证据

### 5.1 选择

采用现有自研 Runtime，新增轻量 Orchestration Controller 和以下策略组件：

- Execution Router；
- Model Router；
- Planner / Plan Validator；
- DAG Scheduler / Join Evaluator；
- Runtime/Workflow/Delegation Executor adapters；
- Runtime Verifier / Bounded Recovery；
- 现有 Budget Ledger 的 orchestration facade；
- 现有 Run Event/Trust Trace 的编排投影。

### 5.2 证据

1. 仓库只有一个 `AgentRuntime`，且已经覆盖普通 Chat 和 Child Runtime。
2. 所有 Tool 已能走统一 Gate/Executor；重写会增加安全旁路面。
3. 委派模块已经提供本需求最昂贵的基础设施：持久 Parent/Child、并发、deadline、取消、有限重试、Result Envelope、预算和前端树。
4. 当前缺口主要是控制面策略，不是底层 Agent Loop 或持久 Worker。
5. 需求明确禁止第二 Runtime、Gate、Context、Plan 或 Delegation，并允许“不迁移”。
6. Checkpoint/Interrupt 不是当前验收目标，LangGraph 最大差异化价值暂时不是必要条件。

## 6. 保留模块

以下模块保持权威，不由新框架替换：

| 模块 | 保留职责 |
| --- | --- |
| `AgentRuntime` | 唯一 Model/Tool Loop |
| Provider Registry/clients | 模型调用、usage、错误映射 |
| Tool/MCP/RAG Registry | 能力发现与执行适配 |
| `PreToolCallGate` / Unified Executor | 权限、风险、确认和 Tool 执行 |
| `RunContext` / Context Builder | Run 隔离、上下文、Todo、Summary/Trim、Memory 引用 |
| `SQLiteRunStore` / SessionStore | 业务状态、事件、Artifact、Outbox 真相 |
| Delegation Service/Dispatcher/Worker | Parent/Child、队列、并发、取消、重试 |
| Budget Ledger | Parent/Child 预算预留、消费、释放、结算 |
| Result Envelope/Validator/Merger | Child 结果权威和确定性合并 |
| Confirmation/Email Approval | 唯一人工确认机制 |
| Trust Store/Trace/Eval/Safety Gate | 可观测、离线评测和发布控制 |
| `/v1/chat`、`/v1/runs`、现有 UI | 兼容入口与完整聊天/运行详情 |

## 7. 新增适配层

新增组件不得拥有独立 Runtime 或 Store：

1. `OrchestrationController`：读取/patch `RunContext` 与 Parent payload，驱动条件状态图。
2. `LegacyRouterAdapter`：把现有三分类和 Skill Selector 转换为 Route Decision signal。
3. `RuntimeExecutorAdapter`：将 State/Step 转为现有 `RunSpec + RunContext`。
4. `WorkflowExecutorAdapter`：调用注册 Workflow/Skill。
5. `DelegationExecutorAdapter`：将已验证 Child Step 转为现有 Task Contract。
6. `OrchestrationBudgetFacade`：给现有 ledger 增加 steps 和 Snapshot 接口。
7. `OrchestrationTraceBridge`：把结构化编排事件投影到现有 Trust Trace。
8. `TaskApiFacade`：task_id 到 Parent Run 的兼容查询，不复制任务数据。

## 8. 最小 Spike 规则

本迭代无需框架 Spike。未来若提出引入 LangChain、LangGraph 或 Agents SDK，必须单独审批并满足：

1. **边界最小**：只选一个明确能力，例如 Provider adapter、图调试或无副作用 specialist；不得接管全 Runtime。
2. **同一 Fixture**：与自研路径使用相同输入、能力快照、价格、Mock Tool、时钟、失败序列和 Rubric。
3. **同一输出契约**：结果转换为现有 Result Envelope/Verify Result。
4. **零 Gate 旁路**：生产 Tool 仍经现有 Gate；不能因框架 tool wrapper 直接执行。
5. **状态单一真相**：现有 RunStore 仍权威；Spike 状态不可驱动生产终态。
6. **量化指标**：质量、引用、tokens、cost、latency、tool/model calls、失败率、开发维护成本。
7. **安全断言**：Approval、Budget、Context 隔离、Trace 脱敏、取消和事件幂等全部通过。
8. **退出标准**：无显著净收益或需复制 Runtime/Gate/Store 时立即停止，不推进迁移。

## 9. Checkpoint 与 Interrupt 决策边界

### 9.1 用途

- Checkpoint：保存图/Runtime 在某步的精确状态，用于恢复、故障容错、状态历史、回放或时间旅行。
- Interrupt：在节点中暂停，暴露待处理信息，并在外部输入后按框架语义恢复；通常需要 Checkpointer。

### 9.2 与现有数据的区别

| 概念 | 保存内容 | 目的 | 是否精确恢复执行位置 |
| --- | --- | --- | --- |
| Summary | 对话/工具信息的语义压缩 | 控制模型上下文 | 否 |
| Memory | 跨会话治理事实/偏好 | 个性化与事实延续 | 否 |
| Todo/Plan | 目标、步骤、依赖和状态 | 控制任务 | 否 |
| Task Snapshot | 后台业务状态、Child/预算/事件摘要 | 查询、取消、继续安全边界 | 否 |
| Checkpoint | Runtime/Graph state snapshot | 原节点恢复/回放 | 是，取决于框架语义 |
| Interrupt | 暂停点 + 外部输入恢复 | 长期 HITL | 依赖 Checkpoint |

### 9.3 未来采用信号

- 真实任务必须跨重启从原节点继续，重新执行未完成 Step 不可接受；
- 人工等待很长且需要保存精确执行栈；
- 节点重放与所有外部副作用已严格幂等；
- 当前状态机维护成本有量化证据超过迁移成本；
- 能完整映射 Gate、Approval、Budget、Trace、Artifact、Parent/Child 和测试；
- Spike 在同一 Fixture 上显示可靠性或维护成本显著改善。

当前不实现 Checkpoint Store、LangGraph Interrupt、跨重启原节点恢复，不为其创建实施任务，也不把它列为当前验收或发布门禁。现有委派 checkpoint 字段属于既存兼容能力，不扩展为通用承诺。

## 10. 状态持久化与兼容策略

1. `RunContext` 作为运行中 State；后台/等待型 State 的受控快照嵌入现有 Parent payload。
2. Plan、Decision 和 Snapshot 过大时存 Artifact，Parent 只保留 ID/ref/hash；仍由同一 RunStore/Artifact Store 管理。
3. 数据模型只做加法演进，使用 schema version 和兼容 decoder；不让新字段破坏旧 Run 查询。
4. `/v1/tasks` 是 `/v1/runs` 的 facade，不建第二任务库。
5. Run Event 是业务状态事件，Trust Trace 是脱敏投影；二者通过稳定 ID 关联，职责不交换。

## 11. 发布与回滚

### 11.1 发布

1. Router shadow mode：只记录新 Decision，不改变旧路径。
2. 对 Direct、Workflow、Tool Loop 分别开 feature flag；验证简单路径无额外 Plan/Child。
3. Plan/Delegation 在固定 Fixture、预算、取消、Join 和 UI 通过后单独启用。
4. Model Router 初期只允许当前默认/显式模型；fallback 由离线 Eval 逐个放开。
5. Safety/Release Gate 基于离线 Eval 决定默认启用，运行时 Verifier 不替代发布判断。

### 11.2 回滚

1. 关闭 orchestration route feature flags，恢复现有 Router/Workflow 兼容路径。
2. 保留新增字段、事件和 Artifact，旧代码通过兼容 decoder 忽略；不做破坏性 schema rollback。
3. 已创建 Background Task 继续由现有 Task Manager 收尾或标记 interrupted，不切到另一 Runtime。
4. 已批准/已执行外部动作不回滚；Approval 仍由现有系统审计。
5. 前端检测缺失新字段时回退到现有 Parent/Child 任务卡，不展示静态替代状态。

## 12. 未选择方案的重新评估条件

| 方案 | 重新评估信号 |
| --- | --- |
| LangChain | 某 Provider/Tool 维护成本显著过高，官方 Integration 可在不接管 Gate/Runtime 的 Adapter 中解决，并通过同一 Fixture |
| LangGraph | 跨重启原节点恢复、长时间 Interrupt、回放/时间旅行成为明确业务需求，且副作用已幂等 |
| OpenAI Agents SDK | OpenAI 专用路径在质量、成本、延迟或维护上有显著优势，且能保留多 Provider fallback、现有 Gate/Store/Trace 契约 |

## 13. 风险与缓解

1. **自研维护成本继续增长**：通过小型 orchestration package、不可变 Schema、条件状态图和 Fixture 限制复杂度。
2. **“参考图模式”演化成隐式框架**：Controller 每次只执行一个 Node，Edge 条件可单测，禁止在单函数内串起所有组件。
3. **双状态真相**：RunContext 是活动 State、RunStore 是持久真相；前端/SSE 不推导状态。禁止另建 Orchestration DB。
4. **未来框架 Spike 偷渡生产**：所有 Spike 置于 Adapter/feature flag 后，默认无生产 Tool 权限。
5. **官方框架快速变化**：决策只依赖稳定能力类别，不硬编码模型或未验证 API；重新评估时重新查阅官方文档。
6. **现有 checkpoint 引发期望混淆**：产品与测试明确当前只保证安全边界重试/明确 interrupted，不保证通用原节点恢复。

## 14. 决策完成标准

该 ADR 经确认后：

- 当前实施以自研显式状态图方案为唯一主路径；
- 不创建 LangChain/LangGraph/Agents SDK 全面迁移任务；
- 不创建 Checkpoint/Interrupt 当前实现或验收任务；
- 所有新组件通过 Adapter 复用现有 Runtime、Gate、RunStore、Budget、Delegation、Trace 和 Eval；
- 未来框架评估必须另开 RFC/Spike，并使用同一 Fixture 与可回滚 feature flag。
