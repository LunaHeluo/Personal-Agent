# L10 · 求职调研任务委派计划

用途：设计确认后生成 `job-application-delegation-task.md`，确认计划后再实现。

---BEGIN---
你是我的 Agent 工程实现协作伙伴。请使用中文工作。

前提：`job-application-delegation-requirements.md` 与 `job-application-delegation-design.md` 已确认。现在只生成 `job-application-delegation-task.md`，不要修改代码。

任务文档必须由有序 Task1 / Task2 / Task3 ... 组成。每个 Task 包含：任务目标、子任务、依赖关系、验收标准、预估复杂度。不要生成“状态”字段。

计划至少覆盖：

1. 审计现有 Runtime、任务、预算、Trace、Gate、Eval、API 和前端入口；额外列出所有直接抓取/解析 JD 网页的 Workflow、Router 分支、Service、Tool、API、前端触发点、输出契约和测试调用方。
2. 实现 Task Contract、Parent/Child Run、Result Envelope 与状态迁移。
3. 实现 Specialist Registry 和两个求职 Specialist 的 Prompt、能力、Schema、版本、启停与最小权限定义。
4. 实现仅供 Coordinator 使用的 `delegate_task` 内部入口；后端根据 Registry 创建真实 Child Run，不返回静态或普通函数包装结果。
5. 实现 Child Context Builder，按 Coordinator、Registry、Runtime、Context Builder 的字段所有权组装初始上下文，并计算 Tool、预算和 deadline 的安全交集。
6. 设计并执行旧网页 Workflow 迁移：多页面/动态页面请求统一改为 `delegate_task(job_web_researcher, task_contract)`；移除旧 Router/API/前端主入口，避免双轨、重复抓取、重复计费和重复写入；保留必要兼容 Adapter 与默认关闭的短期回滚开关。
7. 实现 `job_web_researcher` 网页推进循环：搜索候选链接、打开并等待渲染、展开/进入详情页、提取、校验、翻页或停止，并限制最大页面数、最大步骤与每页超时。
8. 实现网页异常处理和人工接管：加载失败、404、空正文、结构变化、重复页可有限重试或降级；登录、验证码、权限与站点拒绝访问暂停并请求用户处理或返回部分结果。
9. 实现网页上下文治理：原始 HTML、Snapshot、重复 DOM 和中间页面留在 Child Trace/Artifact；单页内容按预算裁剪或总结；主 Context 只接收标准化 JD、来源、缺失与错误。
10. 将现有 `AgentRuntime / AgentLoop` 整理为 Parent 与 Child 共用的执行路径；每次调用新建独立 RunContext，不复制 Agent 对象或新增第二套 Loop。
11. 定义共享基础设施与 Run-scoped State：公共 Model/Tool/Registry/Trace/Artifact/RAG 可复用，messages、memory、todo/plan、tool view、budget、cancellation、summary/trim 和 output buffer 必须隔离。
12. 实现场景级 Tool View：求职调研主 Agent 不接收 Search/Browser/raw RAG 完整 Schema，网页子 Agent 只接收 Search/Browser，简历子 Agent 只接收授权 RAG；并从 Subagent 能力中移除递归委派入口。
13. 实现按 artifact_id、knowledge_scope、chunk_id 加载资料、Tool Schema 过滤和安全交集。
14. 实现 Child 写入隔离、Coordinator 校验合并，以及必要的版本号、锁和幂等保护。
15. 实现 Result Envelope、确定性 Result Validator、冲突/缺失标记和 Merger；主 Context 只吸收受控结果与 Trace 引用。
16. 实现有界 Dispatcher、并发上限、超时、取消传播、幂等和有限重试。
17. 贯通父子 Trace、迁移 route/legacy_path_used、Child 消息/Tool Result 留存、预算分配和 Pre-Tool-Call Gate。
18. 在现有运行详情中展示父子任务、预算、状态、失败与合并证据。
19. 建立固定 Fixture，覆盖旧 Workflow 不再调用、唯一 Subagent 路由、工具 Schema 隔离、兼容输出、回滚开关，以及网页推进和异常案例。
20. 运行单 Agent 与 Multi-Agent 基线比较；只有收益成立才默认启用。
21. 运行真实 Search/Browser Smoke，保留来源、路由证据和父子 Trace。
22. 完成独立验收、失败修复和全量相关回归。

输出任务计划后停止。只有我明确确认计划后，才按顺序实现；每完成一项报告文件、测试、证据与风险。
---END---
