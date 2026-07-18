# L2 · Workflow · 任务书

## 复制区

---BEGIN---
请读：
- starter-agent/evals/acceptance_cases.yaml
- examples/job-hunt-agent/docs/workflow.md.template

任务：根据 core-001、edge-001 和 risk 类验收案例，写 `starter-agent/docs/workflow.md`。

要求：
- 5-7 步，建议结构 Validate -> Gather -> Draft -> Confirm -> Commit。
- 每步标注 D/A/H。
- 缺输入进入 `waiting_for_user`。
- 高风险动作进入 `waiting_for_approval`。
- 无法完成时进入 `failed`，不要编造。
- 文末加“与 acceptance 对照表”。
---END---

## 粘贴后自检

- 是否覆盖正常路径和失败路径。
- 是否写清投递、发邮件、事实修改前必须确认。
- 是否引用了 acceptance case id。

