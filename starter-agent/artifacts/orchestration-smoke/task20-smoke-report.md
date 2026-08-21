# Task20 求职 Smoke 与阶段回归报告

- 日期：2026-08-15（Asia/Shanghai）
- 固定评测前置门禁：通过；68 cases；报告 `artifacts/orchestration-eval/orchestration-fixture-v1-20260815.json`
- 真实只读目标：`https://openai.com/careers/offensive-security-agent-engineer-remote-us/`

## 真实 Smoke

状态：`blocked`

公开职位页已通过只读检索确认存在，但仓库的 `agent trust real-smoke` 会调用当前配置的外部模型，并可能向该模型发送绑定的简历/个人上下文。执行审批因缺少“允许该具体外部模型接收个人求职上下文”的明确授权而拒绝。未绕过审批，未启动 Provider/MCP Child，未投递申请，未发送邮件，且不改变固定 Fixture Release Gate。

结构化阻塞证据：`artifacts/orchestration-smoke/orchestration-real-smoke-20260815-blocked.json`

恢复方式：明确授权当前配置的 Provider/Model 接收 Smoke 提示及可能绑定的简历上下文，或配置隐私批准的测试模型后重跑同一命令。

## 既有第 9 阶段关键回归

- 范围：日志/Trace 脱敏、Context Token、Summary/Memory、MCP Result Guard、Pre-Tool-Call Gate、确认执行屏障、Trust/Delegation Trace。
- 结果：66 passed，0 failed，0 errors，0 skipped。
- 报告：`artifacts/orchestration-smoke/stage9-regression.xml`

## 既有第 10 阶段关键回归

- 范围：Coordinator、挂起/恢复、job web researcher、Parent/Child Runtime、真实启动入口、Result Envelope、Dispatcher/Worker、取消、deadline、有限重试、求职调研编排、邮件工具与 Approval。
- 结果：115 passed，0 failed，0 errors，0 skipped。
- 报告：`artifacts/orchestration-smoke/stage10-regression.xml`

## 高风险邮件路径

- 范围：未确认拒绝、明确批准、拒绝/过期边界、重复发送幂等、consumed replay exactly-once、Mock 邮箱 E2E。
- 结果：5 passed，0 failed，0 errors，0 skipped。
- 报告：`artifacts/orchestration-smoke/email-approval-regression.xml`
- 外部副作用：0 个真实邮件；测试仅使用 Mock/受控适配器。
