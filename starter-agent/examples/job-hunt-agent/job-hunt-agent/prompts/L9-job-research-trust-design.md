# L9 · 求职调研信任层设计文档

用途：需求确认后生成 `job-research-trust-design.md`。本提示词只做设计，不生成任务计划、不修改代码。

---BEGIN---
你是我的 Agent 工程设计协作伙伴。请使用中文工作。

前提：

- `job-research-trust-requirements.md` 已确认。
- 现在只生成 `job-research-trust-design.md`，不要生成任务计划，不要修改代码。

请先阅读现有仓库，核对真实实现：

- Agent Runtime、ContextBuilder、Tool Registry、MCP Client Manager、Skill Registry 与 Pre-Tool-Call Gate。
- Eval、Fixture、Trace、JSONL Log、Token Usage、错误映射与前端路由。
- `job-research` Skill、SerpAPI Tool、Playwright MCP、RAG Tool 和能力目录。

生成的 `job-research-trust-design.md` 必须包含：

- 需求理解与设计目标
- 技术选型
- 总体架构设计
- 模块/组件设计
- 数据模型
- API / 服务接口设计
- 状态流转与交互流程
- 错误处理
- 性能与安全考虑
- 测试策略
- 风险与待确认事项

设计必须说明：

1. Eval Suite、Case、Fixture、Run、Case Result、Assertion Result、Metric、Failure Cluster 和 Release Gate 的数据模型及版本关联。
2. Eval Runner 如何隔离每条 case、装载固定 Fixture、设置随机性、超时、重试和并发，并保证测试之间不共享污染状态。
3. Rule Evaluator、Programmatic Metric、LLM Judge 和 Human Review 的责任；权限、Schema、Tool、来源、引用和执行顺序必须由确定规则验证。
4. 固定 Fixture Eval 与真实 Playwright MCP Smoke 的独立执行与报告路径，禁止把联网变化混入基线。
5. Trace Context 怎样贯穿 eval_run_id、case_id、session_id、turn_id、model_request_id、tool_call_id、policy_decision_id、approval_id 和 child_run_id。
6. 事件模型至少包含 Session、Turn、Model、Tool、Policy、Approval、Memory / Context、Error 和 Run 状态；说明事件顺序、父子关系和幂等写入。
7. 模型请求如何记录 Tool 名称快照、Schema 哈希、Prompt / Skill 版本和 Token，而不默认保存秘密或完整敏感正文。
8. Tool 启停回归如何证明：关闭项只进入轻量能力目录，不进入 callable tools，也没有完整 Description / Input Schema；启用后下一轮请求才原子恢复完整定义。
9. Pre-Tool-Call Gate 回归如何证明权限优先级、白名单自动执行、非白名单确认、仅本次执行、加入白名单、取消、超时、重复点击与强制确认不可绕过。
10. Prompt Injection Fixture 如何模拟网页、PDF、邮件或 Tool Result 中的恶意文字，并通过 Policy 与 Tool Trace 证明没有真实 secret read 或外发动作。
11. 日志脱敏发生在哪一层；Authorization、Token、Cookie、密码、邮箱授权码、简历正文和 Tool Result 敏感字段的处理与保留策略。
12. 指标计算：Task Success、Tool / Argument Accuracy、Citation Correctness、Approval Compliance、P50/P95、Token、Cost per Successful Task；分母、缺失值和失败任务成本如何处理。
13. 失败聚类、根因记录、前后 Run 比较和 Release Gate 决策；安全硬门禁如何覆盖普通平均分。
14. 统一 `Trust Center` 页面及 `Evals`、`Traces`、`Safety` 页签的路由、组件、加载、空、错误、比较和窄屏状态。
15. `Evals` 页的 Suite / Run / Case / Assertion / Metric 展示与运行操作；运行必须调用真实后端并展示进度、取消和失败。
16. `Traces` 页的过滤、树状链路、事件详情、脱敏摘要和从 Case 跳转到 Turn / Tool 的交互。
17. `Safety` 页的策略版本、红队案例、门禁、BLOCKED 原因和证据；策略修改与重新运行的权限和审计。
18. 后端 API、状态机、权限校验、分页、存储与清理策略；前端不得直接计算或篡改最终门禁结论。
19. 单元、集成、端到端与真实 Smoke 测试矩阵，以及每类测试失败后怎样定位和重跑。
20. 从需求确认到真实验收的诊断闭环；任一步失败要保留原始错误、修复并重跑完整相关链路。

约束：

- 优先复用项目现有框架和数据模型；不要平行实现第二套 Agent Runtime、Tool Gate 或日志系统。
- 不要把 Observability Hook 设计成修改业务决策的地方。
- 不要用前端静态数据、Mock 全绿报告或 LLM 自评代替真实 Runner、Trace 和 Gate 证据。
- 不要记录真实秘密；需要验证脱敏时使用带明显测试前缀的假 Token。

输出 `job-research-trust-design.md` 后停止，等待我确认设计。
---END---
