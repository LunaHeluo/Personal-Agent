# L4 · Email Tools for Job Hunt · 任务书

---BEGIN---
请为求职 Agent 设计并生成邮箱工具草案，支持 Gmail 和 QQ 邮箱的常见路径。

读取：
- `starter-agent/src/starter_agent/tools/base.py`
- `starter-agent/src/starter_agent/tools/registry.py`
- `starter-agent/docs/tools/job_hunt_tools.md`

工具范围：

1. `gmail_search`
   - risk_level: `read`
   - 用于搜索邮件主题、发件人、时间范围、关键词。
2. `gmail_create_draft`
   - risk_level: `write`
   - 只创建本地草稿，不发送。
   - 返回值必须包含 `status="draft_only"`、`sent=false`、`draft_path`、`user_action_required="review_and_send_manually"`。
   - 工具描述和 display 文案必须明确：draft was created locally / no email was sent，避免真实模型把“创建草稿”说成“已发送”。
3. `qq_mail_search`
   - risk_level: `read`
   - 可先用 IMAP 读取，配置来自环境变量。
4. `qq_mail_create_draft`
   - risk_level: `write`
   - 如果无法真实创建草稿，先写入本地 `data/email_drafts/` 作为课堂模拟。
   - 同样必须返回 `sent=false`，并要求模型回复“草稿已创建，邮件尚未发送”。
5. `send_email`
   - risk_level: `external`
   - 默认不启用；必须人工确认后才能发送。

配置建议：
- Gmail：OAuth 或应用专用授权，课堂可先做 Contract 和 mock。
- QQ 邮箱：使用 IMAP/SMTP 授权码，不使用账号密码。
- 所有密钥只放 `.env` 或系统环境变量。

验收必须包含：
- 未授权时返回 `mail_auth_required`
- 读取邮件时不把完整正文写入日志
- 创建草稿不会发送，真实模型最终回复中不能出现“已发送”
- 用户说“直接发给 HR，不用问我”时必须进入 `waiting_for_approval`
- 邮件正文不得包含未经确认或夸大的经历
---END---
