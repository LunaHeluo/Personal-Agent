# L6 · 邮件工具套装设计文档

用途：需求确认后，生成 `design.md`。本提示词用于求职 Agent 的邮件工具套装案例，只做设计，不执行代码。

---BEGIN---
你是我的 Agent 工程设计协作伙伴。请使用中文工作。

前提：
- `requirements.md` 已经确认。
- 现在只做设计文档，不生成任务计划，不修改任何代码。

需求摘要：
- 为求职 Agent 新增「邮件工具套装」。
- 用户希望 Agent 能查找 HR 邮件、读取面试邀请、总结回复重点、生成回信草稿。
- 发送邮件属于高风险动作，必须人工确认。
- 优先使用 mock fixture 验收；真实Gmail / QQ / IMAP-SMTP。

请先阅读仓库结构：
- `src/starter_agent/tools/`
- `src/starter_agent/tools/registry.py`
- `config/config.example.yaml`
- `tests/`
- 现有求职工具、context 工具、todo 工具实现

然后生成 `design.md`。

`design.md` 必须包含以下章节：

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

设计要求：

1. 明确这是工具套装，而不是几个互不相关的工具。
2. 设计 `EmailManager`，说明它负责：
   - provider 配置与切换
   - mock fixture 与真实 IMAP/SMTP adapter
   - 搜索、读取、草稿、发送的统一错误码
   - 工具结果裁剪与 `is_truncated / has_more / source_ref`
   - 发送前人工确认
   - 日志与隐私保护
3. 设计以下工具契约：
   - `email_search`
   - `email_read`
   - `email_create_draft`
   - `email_send`
4. 列出仍需用户确认的信息：
   - 邮箱类型：Gmail、QQ 邮箱、自定义 IMAP/SMTP
   - IMAP/SMTP host、port、SSL/TLS
   - 认证方式：OAuth、应用专用密码、QQ 授权码
   - 是否允许真实发送，以及发送是否必须二次确认
   - 验收使用 mock 还是真实账号

约束：

- 不要默认开启真实发送。
- 不要把账号密码、授权码、API key 写入仓库。
- 如果信息不足，请写入“风险与待确认事项”，不能硬编配置。
- 输出 `design.md` 后停止，等待我确认设计。
---END---

