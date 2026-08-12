# L10 · 求职调研任务委派评测集

用途：Runner 与委派机制完成后生成固定评测案例。

---BEGIN---
你是我的 Agent Evaluation 协作伙伴。请使用中文工作。

阅读已确认的需求、设计、真实 Task Contract Schema、Eval Runner、Trace 和两个 Specialist，生成：

- `evals/job-application-delegation-cases.yaml`
- 对应脱敏 Fixture

至少 12 条案例，覆盖：

- 双 Specialist 成功并正确合并。
- job_web_researcher 失败或超时。
- 单个稳定 URL、固定字段、一次 Tool Call 可完成时不启动 Subagent。
- job_web_researcher 对三个候选 JD 执行打开、等待动态渲染、展开详情、提取、完整性检查和继续下一页的多步循环。
- 页面加载失败、404、空正文、重定向、结构变化和重复页按策略有限重试、降级或去重。
- 登录、验证码、权限限制或站点拒绝访问时不绕过限制，暂停请求用户处理，或返回 partial、missing 和明确错误。
- 原始 HTML、Browser Snapshot、重复 DOM 和中间页面只留在 Child Trace/Artifact，主 Agent Context 只增加标准化 jobs[]、source_url、missing、errors、usage 与 child_run_id。
- 符合多页面/动态页面条件的请求只调用一次 `delegate_task(job_web_researcher, ...)`，不会再调用旧网页 Workflow，也不会由主 Agent 直接调用 Browser。
- 迁移后旧 Router、API、Service 和前端入口不会触发旧网页 Workflow；`legacy_path_used=false`，不存在双轨、重复抓取、重复计费或重复写入。
- 单个稳定 URL、固定字段的一次性读取仍可走底层 Tool，但该案例不会错误启动多页面 Subagent。
- 主 Agent 的真实模型请求不包含 Search/Browser/raw RAG 完整 Schema；`job_web_researcher` 只包含 Search/Browser；`profile_evidence_analyst` 只包含授权 RAG。
- 旧调用方需要兼容时，兼容 Adapter 保持输出契约且内部只走 Subagent 新路径；默认关闭的回滚开关单独测试并记录，不作为正常降级路径。
- profile_evidence_analyst 无证据并诚实返回缺口。
- 父任务取消向 Child Run 传播。
- 重复回调与迟到结果不重复合并。
- Child 输出 Schema 不合法。
- source_url 或 chunk_id 冲突被标记。
- Child 请求契约外 Tool 被 Gate 拒绝。
- `delegate_task` 创建了真实 Child Run，Child 拥有独立模型请求、Context 和 Tool Trace，而不是普通函数、Mock 或静态结果包装。
- Parent 与 Child 复用同一个 AgentRuntime/AgentLoop 代码路径，但 RunContext 是不同对象；messages、memory、todo/plan、tool view、budget、cancellation、summary/trim 和 output buffer 不发生跨 Run 污染。
- 启动两个并发 Child 后，一个 Child 的消息追加、计划状态变更、预算消耗、取消或裁剪不会改变另一个 Child 或 Parent 的对应状态。
- 公共 Tool Registry 可以复用，但每个 Child 只接收任务契约、Specialist 与 Policy 交集内的 Tool Schema。
- artifact_id、knowledge_scope 或 chunk_id 按引用加载必要资料；测试不得通过复制完整主会话、JD、简历或其他 Child 结果才能通过。
- 两个 Child 并发写入同一业务对象时，Coordinator 合并或版本/锁/幂等策略能够阻止覆盖与重复。
- Subagent 的 callable tools 中不包含 `delegate_task`，递归委派请求被确定性拒绝。
- Registry 中不存在、已关闭或版本不兼容的 Specialist 不会启动 Child Run。
- 真实 Child 模型请求只包含 Specialist Prompt、Task Contract、允许的 Tool Schema、预算/Policy/Trace 和按引用加载的必要资料；不含完整主 Chat、全部 Memory、其他 Child 中间结果或无关 Tool Schema。
- Coordinator 试图覆盖 System Prompt、扩大 Tool 或超过 Parent 剩余预算时，Runtime 使用安全交集或拒绝，不按模型请求放宽。
- Child 返回符合 Result Envelope；主 Agent Context 只新增 output、evidence、missing、conflicts、usage 和 child_run_id，不复制完整 Child Messages 或原始 Tool Result。
- Child 或 Parent 预算耗尽后安全停止。
- 单 Agent 在简单任务上更快、更便宜，Router 不应拆分。
- 固定 Fixture 下单 Agent 与 Multi-Agent 的时间、Token、成本和质量比较。

Tool、权限、Schema、父子状态、取消和合并顺序使用确定性断言；LLM Judge 只能评价规则难覆盖的表达质量。案例不得依赖变化的互联网。生成后执行 Runner 的 schema/dry-run 检查，修正到可真实读取，并输出覆盖矩阵与运行命令。
---END---
