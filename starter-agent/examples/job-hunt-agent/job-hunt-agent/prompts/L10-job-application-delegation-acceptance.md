# L10 · 求职调研任务委派最终验收

用途：实现完成后生成 `docs/job-application-delegation-acceptance.md`。

---BEGIN---
你是我的 Agent 功能验收协作伙伴。请使用中文工作。

独立阅读需求、设计、任务计划、实现、评测集、Trace、日志、前端运行详情和测试，执行真实验收并生成 `docs/job-application-delegation-acceptance.md`。

必须验证：

1. 简单任务保持单 Agent；复杂求职调研才进入有界委派。
2. `delegate_task` 只向 Coordinator 暴露；调用后由后端根据 Specialist Registry 创建真实 Child Run。
3. Child Run 有独立 System Prompt、模型请求、Context、Tool 集、预算和多轮 Agent Loop 证据，不是普通函数、Mock 或静态结果包装。
4. Parent 与 Child 复用同一个 AgentRuntime/AgentLoop 代码路径，但每次 Run 新建独立 RunContext；代码中不存在复制 Parent Agent 对象或为 Subagent 重建第二套 Loop 的实现。
5. 检查对象身份和状态变更，证明 Parent 与不同 Child 的 messages、memory、todo/plan、tool view、budget、cancellation、summary/trim 和 output buffer 不共享、不串写。
6. 检查真实 Child 模型请求，证明初始上下文由 Coordinator 任务字段、Registry 能力定义、Runtime 边界和 Context Builder 必要资料组成，并通过 artifact_id、knowledge_scope 或 chunk_id 按引用加载；不含完整主 Chat、全部 Memory、其他 Child 中间结果或无关 Tool Schema。
7. Coordinator 无法覆盖 Specialist System Prompt、扩大 Tool 权限或分配超过 Parent 剩余量的预算；Subagent 不含递归委派入口。
8. 两个 Specialist 职责不重叠、Tool 最小化，且只收到最小上下文包。
9. `job_web_researcher` 能真实完成“搜索候选链接 → 打开/等待渲染 → 展开或进入详情页 → 提取 → 校验 → 下一页/停止”的多步循环；单页稳定读取仍由普通 Tool 完成。
10. 审计证据列出迁移前的直接网页 Workflow 及全部调用入口；迁移后多页面/动态页面请求只进入 `delegate_task(job_web_researcher, ...)`，旧 Workflow、主 Agent 和前端不会直接抓取网页。
11. 运行计数与 Trace 证明没有双轨、重复搜索、重复抓取、重复计费或重复写入；正常请求中 `legacy_path_used=false`。
12. 单页稳定读取 Tool 仍可作为 Subagent 底层能力或明确的一次性单页路径；兼容 Adapter 保持输出契约但内部只走新路径，回滚开关默认关闭。
13. 主 Agent 的真实模型请求不包含 Search/Browser/raw RAG 完整 Schema；网页子 Agent 只包含 Search/Browser；简历子 Agent 只包含授权 RAG，越权调用被 Gate 拒绝。
14. 网页加载失败、404、空正文、结构变化和重复页有有限恢复；登录、验证码、权限限制和站点拒绝访问会暂停请求用户处理或返回部分结果，不绕过限制。
15. 原始 HTML、Snapshot、重复 DOM 和中间页面留在 Child Trace/Artifact，主 Agent 只收到标准化 JD、来源、缺失、错误和用量。
16. Task Contract 字段完整；Child 返回受控 Result Envelope，主 Context 不复制完整 Child Messages、隐藏推理或原始 Tool Result。
17. 并发 Child 对共享业务数据的写入经过 Coordinator 合并或版本、锁、幂等保护，不发生覆盖与重复。
18. 并发上限、超时、取消传播、幂等、有限重试和部分失败真实生效。
19. 失败或超时时结果包含 partial、missing、conflicts，不编造缺失字段。
20. Child Tool Call 仍经过 Pre-Tool-Call Gate，不能调用契约外 Tool。
21. Trace 能从 Parent Run 追到 Child Task、Child Run、Model、Tool、Policy、Approval 与 Merge。
22. 固定评测至少 12 条可重复运行；单 Agent/Multi-Agent 对比报告包含时间、Token、成本、质量和失败复杂度。
23. 使用真实模型、Search 与 Playwright MCP 完成一次公开 JD Smoke，并与固定基线分开。
24. 运行详情使用真实后端状态，刷新、取消、错误和部分结果一致。

失败时继续定位、修复并重跑；外部阻塞则记录原始错误和最小用户动作。最终给出通过项、失败项、未执行项、证据路径、剩余风险和 Release Gate：PASS / PARTIAL / BLOCKED。只有关键契约、权限、父子 Trace、失败处理、固定评测和真实 Smoke 都通过时才允许 PASS。
---END---
