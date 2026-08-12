# L10 · 求职调研任务委派需求

用途：生成 `job-application-delegation-requirements.md`，只做需求澄清，不修改代码。

---BEGIN---
你是我的 Agent 功能开发协作伙伴。请使用中文工作。

我要在现有求职 Agent 上增加「有边界的任务委派」能力。目标场景是：

> 用户要求调研悉尼的 Agent 工程师岗位，并结合自己的简历证据生成投递优先级、匹配依据和能力缺口。

现有 Search Tool、Playwright MCP、RAG、job-research Skill、Pre-Tool-Call Gate、Eval Runner、Trace 与 Safety Gate 必须复用。先审查真实仓库，再向我提出最多 5 个必要问题；优先确认现有异步运行模型、任务状态、Trace ID、预算、取消、前端运行详情和测试入口。

第一阶段只生成 `job-application-delegation-requirements.md`，必须包含：

- 需求背景
- 功能范围
- 目标用户与使用场景
- 用户故事
- 功能需求
- 非功能需求
- 验收标准
- 边界情况
- 风险与待确认事项

功能需求至少覆盖：

- 先用单 Agent 基线和可量化收益判断是否需要 Multi-Agent；允许结论为不采用。
- Coordinator 只负责拆分、分配、预算、取消、收集、校验和合并，不替 Specialist 完成全部工作。
- 为 Coordinator 提供内部委派入口，例如 `delegate_task(specialist_id, task_contract)`；该入口可以采用 Tool Call 形式触发，但必须由后端创建真实 Child Run，不能用普通函数或静态结果冒充 Subagent。
- 建立 Specialist Registry，登记稳定 ID、System Prompt、能力标签、允许 Tool、输入输出 Schema、版本、启停状态和默认预算；Coordinator 只能委派已注册且启用的 Specialist。
- Parent 与所有 Child 必须复用现有无状态 `AgentRuntime / AgentLoop` 的代码路径，不为 Subagent 复制第二套 Loop；每次运行必须新建独立 `RunContext`，不得复制或复用 Parent Agent 对象及其可变状态。
- `job_web_researcher` 只使用岗位搜索与 Browser 能力：Search 先获得候选 JD 链接，随后由该 Subagent 持续打开页面、等待动态渲染、展开详情、进入必要的详情页或下一页、提取目标字段并检查完整性，最终输出带来源的 JD 事实。
- 明确普通网页 Tool 与 `job_web_researcher` 的边界：单个稳定 URL、固定字段、一次调用即可返回时使用 Tool；需要跨页面持续推进、根据页面观察决定下一步、处理异常并压缩大量网页内容时使用 Subagent。
- `job_web_researcher` 的初始上下文只包含 URL/查询条件、目标字段、页面数量上限、停止条件和返回 Schema，不包含完整主 Chat、简历、投递计划或其他无关上下文。
- `job_web_researcher` 必须处理页面加载失败、404、动态渲染超时、选择器失效、空正文、重复页面、重定向、登录、验证码、权限限制和站点拒绝访问：可恢复错误有限重试或换入口；登录、验证码、权限和禁止访问必须暂停并请求用户处理，或返回 partial、missing 与明确错误，不得绕过安全限制。
- 原始 HTML、Browser Snapshot、导航菜单、重复正文和中间页面不得回填主 Agent Context；它们留在 Child Trace/Artifact 中，主 Agent 只接收标准化 jobs[]、source_url、missing、errors、usage 与 child_run_id。
- 先审计真实仓库，定位当前所有“主 Agent 或固定 Workflow 直接抓取/解析 JD 网页”的入口，包括 Router 分支、Workflow、Service、API、Tool 调用、前端触发点和测试；在需求文档中列出旧入口、调用方、输出契约和迁移影响。
- `job_web_researcher` 必须替换现有直接抓取网页的 Workflow，成为多页面、动态页面和需要异常处理的 JD 网页调研唯一主路径；符合条件的请求必须通过 `delegate_task(job_web_researcher, task_contract)` 进入真实 Child Run。
- 旧网页 Workflow 不得与 Subagent 双轨运行，不得继续被 Router、主 Agent、API 或前端作为默认/备用抓取入口调用，也不得造成重复搜索、重复抓取、重复计费或重复写入。
- 保留单页稳定读取所需的底层网页 Tool，但它只作为 `job_web_researcher` 的内部能力，或明确的一次性单页 Tool 路径；在多页面求职调研路径中，主 Agent 的模型请求不得暴露 Search/Browser 完整 Tool Schema。
- 迁移必须处理旧调用方与输出契约：优先保持兼容；必须变更时提供明确版本迁移。允许提供默认关闭的短期回滚开关，但正常路径不得回退旧 Workflow，且必须记录 route、legacy_path_used、child_run_id 和迁移后的调用证据。
- 工具归属按角色隔离：主 Agent 在求职调研路径只保留 `delegate_task`、结果检查/合并和用户确认能力；`job_web_researcher` 只获得 Search/Browser；`profile_evidence_analyst` 只获得授权 RAG。未分配给角色的完整 Tool Schema 不得进入该角色的模型请求。
- `profile_evidence_analyst` 只从授权简历知识库读取证据，不能补写经历。
- 每个 Child Task 必须具有稳定 task_id、goal、inputs、output_schema、allowed_tools、deadline、token/cost budget、failure_behavior 和 parent_run_id。
- 明确 Child 初始入参的字段所有权：Coordinator 只提供 specialist_id、goal、必要 inputs、constraints 和 failure_behavior；Registry 提供 System Prompt、allowed_tools、output_schema、version 与默认限制；Runtime 注入 Parent/Child IDs、最终 deadline/budget、policy、trace context 和 idempotency key；Context Builder 按引用加载必要资料。
- 子 Agent 只获得最小上下文包，不复制完整对话、长期记忆或全部 Tool Schema。
- 每个 RunContext 必须独立持有 messages、working memory、todo/plan、effective tool view、预算、取消信号、summary/trim 状态和输出缓冲；Parent 与不同 Child 之间不得发生状态串写。
- Model Client、Tool 实现、Specialist/Tool Registry、Trace Store、Artifact Store 与 RAG 服务可以作为公共基础设施复用；Tool Registry 虽然共享，Child 可见的 Tool Schema 仍必须经过任务契约、Specialist 配置与 Policy 的安全交集过滤。
- 跨 Run 传递资料优先使用 artifact_id、knowledge_scope、chunk_id 等引用，由 Context Builder 按权限加载必要片段；不得为方便而把完整 JD、简历、主会话或其他 Child 结果复制进每个 Context。
- Child 对共享业务数据的写入必须隔离：先产生候选结果，再由 Coordinator 校验合并；需要直接写入时使用版本号、锁或幂等键，避免并发覆盖和重复。
- Coordinator 不得临时覆盖 Registry 中的 System Prompt、扩大 Tool 权限或提高到超过 Parent 剩余量的预算；最终 allowed_tools 必须是 Specialist、当前策略和任务契约允许范围的交集。
- 支持有上限的并行、超时、父任务取消、幂等重试、部分失败、结果去重和确定性合并。
- 合并结果保留 task_id、source_url、chunk_id、缺失项和冲突，不静默覆盖，不用模型补齐失败字段。
- Child 只向 Parent 返回受控 Result Envelope：status、结构化 output、evidence、missing、conflicts、usage 和 child_run_id；完整 Child 对话、隐藏推理与原始日志不进入主 Agent Context。
- 每个 Child Run 关联 Eval Case、Session、Turn、Tool、Policy、Approval 与父 Run。
- Child Run 必须拥有独立 System Prompt、Context、Tool 集和预算，并能执行受限的多轮 Model → Tool → Observation；用户仍只面对原 Chat。
- 默认只有 Coordinator 拥有委派入口，Subagent 不得递归创建其他 Subagent；若未来支持递归，必须另行定义最大深度、Child 总数与共享预算。
- 所有 Tool Call 继续经过现有 Pre-Tool-Call Gate；子 Agent 不能继承超出契约的权限。
- 在现有 Trace/Trust Center 中查看父子任务树、状态、预算、失败原因和合并结果，状态来自真实后端。
- 固定 Fixture 评测与真实 Search/Browser Smoke 分开记录。

至少定义以下验收场景：双成功、一个失败、一个超时、父任务取消、重复回调、Schema 不合法、来源冲突、权限拒绝、预算耗尽、单 Agent 更优。

不要新增第二套 Agent Runtime、权限 Gate、Trace 或预算系统。不要用多个模型自由聊天代替任务契约。输出需求文档后停止，等待我确认。
---END---
