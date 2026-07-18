# 求职邮件工具套装

## 工具与风险等级

| 工具 | 风险等级 | 行为 |
|---|---|---|
| `email_search` | `read` | 结构化搜索已授权邮箱，不改变已读状态 |
| `email_read` | `read` | 按 opaque `message_ref` 使用 PEEK 读取邮件 |
| `email_create_draft` | `write` | 创建本地、mock 或邮箱草稿，绝不发送 |
| `email_send` | `external` | 仅发送已存在且经过服务端草稿级审批的单封草稿 |

四个工具共享同一个 `EmailManager`。Manager 统一处理 profile、adapter、错误码、结果裁剪、opaque 引用、幂等、审批及隐私边界。

## 安全默认值

- 默认 profile 为 `mock`。
- 默认配置只启用搜索、读取和草稿工具。
- `email_send` 与 `external` 风险等级默认不启用。
- 真实 profile 的 `real_send_enabled` 默认是 `false`。
- 草稿结果始终包含 `sent=false`；创建草稿不代表邮件已发送。
- mock 发送只返回 `status="simulated_sent"` 与 `external_delivery=false`。
- 真实发送必须同时通过 ToolPolicy、profile 发送开关、草稿指纹和一次性服务端审批。

## Mock 配置

```yaml
tools:
  enabled:
    - email_search
    - email_read
    - email_create_draft
  allow_risk_levels:
    - read
    - write
  email:
    active_profile: mock
    body_max_chars: 12000
    attachment_root: tests/fixtures/email/attachments
    profiles:
      mock:
        adapter: mock_fixture
        fixture_root: tests/fixtures/email
        real_send_enabled: false
```

## 真实 IMAP/SMTP 配置

真实连接参数必须由邮箱服务的实际配置确定，不能猜测或硬编码。YAML 只保存非敏感字段和环境变量名称：

```yaml
tools:
  email:
    active_profile: personal
    profiles:
      personal:
        adapter: imap_smtp
        mailbox_type: custom
        account_env: EMAIL_ACCOUNT
        auth:
          type: app_password
          credential_env: EMAIL_CREDENTIAL
        imap:
          host: <由用户确认>
          port: <由用户确认>
          transport: <ssl_tls 或 starttls>
        smtp:
          host: <由用户确认>
          port: <由用户确认>
          transport: <ssl_tls 或 starttls>
        drafts_mailbox: <由用户确认>
        real_send_enabled: false
```

真实账号、应用专用密码、QQ 授权码和 OAuth 凭据只放在未提交的 `.env`、系统环境变量或 secret manager。不得把真实值写入 YAML、代码、日志、fixture 或测试快照。

## 审批流程

1. `email_create_draft` 生成草稿和组合指纹。
2. UI 调用 `POST /v1/email/drafts/{draft_id}/approval-challenges` 获取完整预览。
3. 用户核对收件人、主题、正文和附件。
4. UI 调用 `POST /v1/email/approval-challenges/{approval_id}/confirm` 明确确认。
5. `email_send` 引用该 `approval_id`，Manager 在服务端验证会话、profile、草稿及全部指纹。
6. 草稿发生任何关键变化后，旧审批失效。

模型不能通过传入 `confirmed=true` 创建发送许可。多封邮件必须逐封审批。

## 结果完整性

搜索和读取结果始终包含：

- `is_truncated`：当前内容是否被裁剪。
- `has_more`：是否存在下一页、更多线程内容或可回查内容。
- `source_ref`：会话与 profile 绑定的 opaque 来源引用。

邮件领域裁剪发生在通用 `ToolResultGuard` 之前；Runtime 再次裁剪时会增加 `raw_source_ref`，但不会把裁剪结果误报为完整结果。

## 稳定错误码

常用错误包括：

- `email_profile_not_found`
- `email_missing_credentials`
- `email_authentication_failed`
- `email_capability_not_supported`
- `email_message_not_found`
- `email_placeholder_present`
- `email_attachment_not_found`
- `email_approval_required`
- `email_approval_invalid`
- `email_draft_changed`
- `email_real_send_disabled`
- `email_send_status_unknown`

错误结果是合法 `ToolResult`，不包含 provider 原始异常、凭据、完整地址或邮件正文。
