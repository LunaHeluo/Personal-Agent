# 邮件工具套装验收记录

## 验收范围

- Mock fixture 搜索与分页。
- 无副作用邮件读取。
- 长正文裁剪及完整性字段。
- 草稿创建与附件检查。
- 草稿级审批和发送门禁。
- Mock 模拟发送与幂等。
- IMAP/SMTP fake client 合约。
- 配置缺失与稳定错误码。
- 日志、Context 和长期记忆隐私边界。
- Tool Registry 与 API 集成。

## 自动化验收命令

```powershell
& '.venv\Scripts\python.exe' -m pytest -p no:cacheprovider
```

## 自动化验收结果

- 验收日期：2026-07-17。
- 完整测试套件：`146 passed`。
- 非阻塞警告：1 条，为 FastAPI `TestClient` 依赖的 Starlette/httpx 弃用提示，与邮件工具行为无关。
- 邮件核心集成测试：搜索、读取、长正文裁剪、草稿、审批 API、未审批拒绝和 mock 模拟发送均通过。
- IMAP/SMTP：fake client contract、PEEK、缺凭据、Drafts capability、SMTP 幂等和发送状态未知路径均通过。
- 隐私：测试凭据、地址、主题和正文标记在日志中零命中。

## 核心验收判据

1. Mock 搜索能命中脱敏 HR/面试邀请 fixture。
2. `email_read` 使用 PEEK 语义；读取前后未读标记不变。
3. 长正文返回 `is_truncated=true`、`has_more=true` 和非空 `source_ref`。
4. `email_create_draft` 返回 `draft_only` 与 `sent=false`，adapter send 调用为零。
5. 缺少或伪造审批时 `email_send` 被拒绝，adapter send 调用为零。
6. 草稿内容、收件人或附件变化后旧审批失效。
7. Mock 发送只返回 `simulated_sent` 与 `external_delivery=false`。
8. 缺 profile、凭据、连接配置或 capability 时返回稳定错误码。
9. 日志、异常、ToolResult 和测试输出不包含测试凭据或完整邮件隐私。
10. 发送成功依据 adapter/邮箱回执，不依据模型文本。

## 真实账号验收前置条件

用户已授权使用本地 `.env` 中的 IMAP/SMTP 验收凭据，但真实验收仍需要以下非敏感信息全部明确：

- 邮箱类型。
- IMAP host、port、SSL/TLS 或 STARTTLS。
- SMTP host、port、SSL/TLS 或 STARTTLS。
- 认证方式及 YAML 中对应的环境变量名称。
- Drafts mailbox 名称。
- 专用测试收件人。
- 每封测试邮件的最终内容确认。

如果任何前置条件缺失，不执行网络连接或真实发送，不猜测配置；保留 mock 和 fake client 的可重复验收结果。

## 本次真实账号验收结论

本次未执行真实网络连接或真实发送。只读取了项目根目录 `.env` 的变量名称，没有读取任何变量值；当前可见变量名仅覆盖模型 provider 和 SerpAPI，未检测到 `EMAIL_ACCOUNT`、`EMAIL_CREDENTIAL` 或其他可识别的 IMAP/SMTP 变量名。邮箱类型、host、port、传输方式、Drafts mailbox 和专用测试收件人也尚未配置。

因此系统保持 `real_send_enabled=false`，`email_send` 不在默认启用工具列表中。待上述非敏感配置明确且邮件变量名可解析后，再执行逐封人工确认的真实收取与发送验收。
