# L5 · Todo Plan Tool · 任务书

---BEGIN---
请为求职 Agent 设计 Todo Plan 管理工具。这里的 todo 是 plan 的表现形式，用来辅助 Context 管理。

为什么需要：
求职任务不是一次问答。一个岗位从发现到投递，通常包括：读 JD、匹配简历、修改简历、写邮件、等待确认、投递、记录反馈。Agent 需要显式 todo，而不是把所有状态藏在对话里。每一轮对话都应该把当前 todo 摘要注入 Context，避免模型在长任务里丢方向。

工具：
1. `todo_create`
2. `todo_update`
3. `todo_list`
4. `todo_complete_step`

字段：
- `todo_id`
- `goal`
- `steps[]`
  - `step_id`
  - `title`
  - `status`: pending / running / waiting_for_user / waiting_for_approval / completed / failed
  - `owner`: agent / user
  - `evidence`
  - `updated_at`

验收：
- 用户说“帮我完成这个岗位投递准备”，Agent 能创建 todo plan。
- 缺简历时对应 step 进入 `waiting_for_user`。
- 邮件发送前对应 step 进入 `waiting_for_approval`。
- 用户确认后才能完成 external action。
- 下一轮用户继续对话时，Agent 能看到当前 todo 状态并续做，而不是重新开始。
---END---
