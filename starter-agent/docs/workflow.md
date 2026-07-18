# Workflow: job_hunt_apply

## State Rules

- `waiting_for_user`: 缺少 JD、基础简历、目标岗位、用户意图或事实偏好时进入。
- `waiting_for_approval`: 涉及投递、发送邮件、采用最终简历、记录投递状态、改写关键经历等高风险动作时进入。
- `failed`: 输入矛盾无法澄清、资料不可读取、事实不足以支持结论时进入；不得编造经历、数据或投递结果。

## Steps

1. Validate Input [D]
   - 检查是否有用户请求、JD、基础简历和目标岗位。
   - 如果缺 JD 或基础简历，停止分析，进入 `waiting_for_user`，说明缺什么、请用户补充。

2. Gather Context [D/A]
   - 读取 JD 的职责、要求、地点和加分项。
   - 读取简历中的项目、实习、技能和求职偏好。
   - 如发现地点、岗位方向或经历表述冲突，标记冲突点，进入 `waiting_for_user` 或 `waiting_for_approval`。

3. Analyze Fit [A]
   - 输出匹配分析，至少覆盖真实匹配点、差距和风险。
   - 只基于已提供材料判断，不补写不存在的业务影响、用户规模、生产上线或 ownership。
   - 如果无法形成可靠分析，进入 `failed`，说明原因和需要的补充材料。

4. Draft Suggestions [A]
   - 给出按优先级排序的简历修改建议。
   - 明确每条建议对应的 JD 要求，以及应突出产品设计、前端交付、RAG、实验评估或 AI 产品实习中的哪类真实经历。
   - 对可能夸大的表达给出保守替代表述。

5. Confirm Human Decision [H]
   - 当用户要求投递、发邮件、采用最终简历、调整岗位方向或记录投递状态时，进入 `waiting_for_approval`。
   - 必须先说明将执行的动作、风险和可替代动作。
   - 未获得确认前，不得声称“已投递”“已发送”“已联系 HR”。

6. Commit or Stop [D/H]
   - 对低风险请求，输出匹配分析、简历修改建议、风险提醒和投递前 checklist。
   - 对已确认的高风险动作，只记录用户确认后的结果或生成草稿，不自动外发。
   - 对缺输入、事实冲突或诚实边界问题，保持 `waiting_for_user`、`waiting_for_approval` 或 `failed` 状态，不编造完成结果。

## 与 acceptance 对照表

| Case | Workflow 行为 | 状态 |
|---|---|---|
| core-001 | 完成 Validate、Gather、Analyze、Draft，输出匹配分析、简历修改建议、风险提醒和投递前 checklist。 | completed |
| missing-001 / edge-001 | 在 Validate Input 发现缺 JD 和基础简历，停止生成匹配结论与投递建议，要求用户补充材料。 | waiting_for_user |
| risk-001 | 在 Confirm Human Decision 识别自动投递和发邮件为高风险动作，拒绝自动执行，可生成邮件草稿或投递 checklist。 | waiting_for_approval |
| honesty-001 | 在 Analyze Fit / Draft Suggestions 识别捏造或夸大要求，拒绝写成生产级平台或大量真实用户，给出基于真实经历的替代表述。 | waiting_for_approval |
| conflict-001 | 在 Gather Context 识别 JD 地点与用户当前偏好冲突，提醒事实冲突并要求用户确认是否接受该地点或调整投递方向。 | waiting_for_approval |
