# 求职 Agent 工具清单与 Contract

本文定义求职 Agent 的工具边界、输入输出、凭据管理和验收要求。工具实现应遵循
`Tool` / `ToolResult` 协议，通过 `ToolRegistry` 注册，并受 `ToolPolicy` 与 Runtime
预算约束。本文是设计规范，不代表工具已经实现或已获得外部账号授权。

## 为什么不能只依赖 Prompt

- 岗位信息具有时效性。模型知识可能过期，搜索结果必须带来源 URL、抓取时间和
  发布时间（如果来源提供），否则无法核实岗位是否仍有效。
- 简历事实必须来自用户授权的真实文件。Prompt 只能约束模型不要编造，不能证明
  某段经历确实存在；读取、定位和修改必须以文件内容及版本指纹为依据。
- 邮件涉及隐私和外部副作用。搜索邮件、创建草稿和真正发送是三种不同权限，不能
  用一条 Prompt 混在一起，更不能把“生成了正文”误报成“已经发送”。
- API key、OAuth token 和账号密码需要集中存储、按 profile 切换并在日志中脱敏。
  YAML 和工具代码只能引用凭据名称，不能包含真实凭据。

## 通用约定

### 风险与确认

| 风险 | 含义 | 默认行为 |
|---|---|---|
| `read` | 读取文件、公开搜索或只读邮箱查询 | 可在授权范围内执行 |
| `write` | 创建本地补丁或邮箱草稿，但不对外发送 | 展示变更内容；关键经历改写仍需用户确认 |
| `external` | 向外部收件人发送内容或产生不可忽略的外部影响 | 必须逐次人工确认 |

`send_email` 不得与创建草稿合并。确认必须绑定草稿 ID、收件人、主题、正文摘要和
附件指纹；修改其中任一项后，旧确认失效。

### 统一结果封装

所有工具使用现有 `ToolResult` 外层结构：

```json
{
  "ok": true,
  "data": {},
  "display": "适合用户阅读的摘要",
  "error_code": null,
  "retryable": false,
  "metadata": {
    "source": "非敏感来源标识",
    "observed_at": "2026-07-12T12:00:00Z"
  }
}
```

工具不得把 API key、OAuth access/refresh token、邮箱密码、完整邮件正文或完整简历
写入日志。日志默认只记录工具名、耗时、`ok`、错误码、profile 名、结果数量、文件
指纹或外部对象 ID。面向模型的结果遵循最小披露原则。

### 凭据与 profile

- YAML 只保存环境变量名称、profile 映射和 active profile，不保存真实 secret。
- 真实 secret 放在项目根目录 `.env`（不得提交）或系统环境变量中；生产环境优先使
  用 secret manager 注入环境变量。
- profile 切换由 settings 统一解析，Tool 构造函数只接收已解析的凭据对象，禁止在
  Tool 代码中散落 `os.getenv()`、明文 key 或密码。
- 结果允许暴露 profile 名、环境变量名、邮箱地址的脱敏形式、scope 和 token 过期
  时间；禁止暴露任何真实 secret。
- 缺少凭据必须失败并返回 `missing_api_key` 或 `missing_credentials`，不得返回模拟的
  成功数据。

## Contract 1：`search_jobs_serpapi`

### 用户价值与必要性

搜索公开岗位并保留可核验来源和查询时间。只用 Prompt 会受到模型知识截止时间和
幻觉影响，无法证明岗位存在、仍开放或来自哪个页面。

### 输入 Schema

```json
{
  "type": "object",
  "properties": {
    "query": {"type": "string", "minLength": 2, "maxLength": 300},
    "location": {"type": "string", "maxLength": 100},
    "remote": {"type": "boolean", "default": false},
    "page": {"type": "integer", "minimum": 1, "maximum": 5, "default": 1},
    "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
    "key_profile": {"type": "string", "enum": ["primary", "backup"]}
  },
  "required": ["query"],
  "additionalProperties": false
}
```

### 输出 Schema

```json
{
  "type": "object",
  "required": ["query", "results", "searched_at", "api_key_profile", "api_key_env"],
  "properties": {
    "query": {"type": "string"},
    "searched_at": {"type": "string", "format": "date-time"},
    "api_key_profile": {"type": "string"},
    "api_key_env": {"type": "string"},
    "results": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["title", "source_url", "source_name", "observed_at"],
        "properties": {
          "title": {"type": "string"},
          "company": {"type": ["string", "null"]},
          "location": {"type": ["string", "null"]},
          "summary": {"type": "string"},
          "source_url": {"type": "string", "format": "uri"},
          "source_name": {"type": "string"},
          "published_at": {"type": ["string", "null"]},
          "observed_at": {"type": "string", "format": "date-time"}
        }
      }
    }
  }
}
```

- 数据源：SerpAPI 的公开搜索结果；结果是线索，不等同于已验证仍可投递的岗位。
- 风险等级：`read`。
- 幂等性：同一查询没有副作用，但结果会随时间变化，不保证内容幂等。
- 超时：建议 15 秒；网络错误可重试一次，429 尊重 `Retry-After`。
- 错误码：`invalid_arguments`、`missing_api_key`、`authentication_failed`、
  `rate_limited`、`quota_exceeded`、`provider_timeout`、`provider_unavailable`、
  `invalid_provider_response`。
- 脱敏：URL 中移除 key 和追踪参数；日志不记录原始响应全文、真实 key 或完整查询中
  可能包含的个人信息。

### SerpAPI 密钥设计

YAML：

```yaml
tools:
  serpapi:
    active_key: primary
    key_profiles:
      primary: SERPAPI_API_KEY
      backup: SERPAPI_API_KEY_BACKUP
```

真实值只允许存放在 `.env` 或系统环境变量：

```dotenv
SERPAPI_API_KEY=真实主Key
SERPAPI_API_KEY_BACKUP=真实备用Key
```

解析优先级：工具调用显式 `key_profile`（如果产品允许本次覆盖）→
`SERPAPI_ACTIVE_KEY` → `tools.serpapi.active_key`。profile 必须存在于
`key_profiles`；`primary` 映射 `SERPAPI_API_KEY`，`backup` 映射
`SERPAPI_API_KEY_BACKUP`。结果和日志可以记录 `api_key_profile`、
`api_key_env`，不能记录真实 key。当前 profile 对应环境变量缺失或为空时返回：

```json
{
  "ok": false,
  "error_code": "missing_api_key",
  "display": "当前 SerpAPI 凭据未配置",
  "metadata": {
    "api_key_profile": "backup",
    "api_key_env": "SERPAPI_API_KEY_BACKUP"
  }
}
```

不得回退到假数据，也不得在未声明的情况下自动改用另一个 profile。

### 验收

- pytest：Schema 边界、URL 脱敏、超时/401/429、空结果、缺 key、primary/backup
  解析和 `SERPAPI_ACTIVE_KEY` 覆盖。
- 人工：分别使用 primary 与 backup 搜索；确认来源 URL 和时间存在；检查日志、结果
  和验收截图中没有真实 key。

## Contract 2：`read_resume`

### 用户价值与必要性

从用户明确授权的真实文件读取简历，为匹配和修改建立事实基线。Prompt 无法访问文件
或证明内容来源，模型记忆也不能代替文件版本。

### 输入 Schema

```json
{
  "type": "object",
  "properties": {
    "path": {"type": "string", "minLength": 1},
    "format": {"type": "string", "enum": ["auto", "md", "txt", "docx", "pdf"], "default": "auto"},
    "max_chars": {"type": "integer", "minimum": 1000, "maximum": 100000, "default": 50000}
  },
  "required": ["path"],
  "additionalProperties": false
}
```

### 输出 Schema

```json
{
  "type": "object",
  "required": ["document_id", "file_name", "sha256", "text", "truncated"],
  "properties": {
    "document_id": {"type": "string"},
    "file_name": {"type": "string"},
    "format": {"type": "string"},
    "sha256": {"type": "string"},
    "text": {"type": "string"},
    "sections": {"type": "array", "items": {"type": "object"}},
    "truncated": {"type": "boolean"}
  }
}
```

- 数据源：用户指定且位于允许目录内的本地文件；不得任意扫描用户目录。
- 风险等级：`read`。
- 幂等性：文件内容不变时幂等，以 SHA-256 作为版本依据。
- 超时：本地文本 5 秒，DOCX/PDF 解析 20 秒。
- 错误码：`file_not_found`、`path_not_allowed`、`unsupported_format`、
  `file_too_large`、`parse_failed`、`encrypted_document`。
- 凭据：不需要外部凭据。
- 脱敏：日志只记录相对文件名、类型、大小、指纹和解析状态；不得记录简历正文、电话、
  邮箱、地址、证件号。向模型返回正文是完成用户当前任务所必需的授权披露，不得进入
 稳定日志或跨会话缓存。
- 验收：pytest 覆盖路径穿越、格式识别、截断、指纹稳定性和解析错误；人工确认抽取内容
  与原文件一致，日志不包含简历隐私。

## Contract 2A：`compare_resume_to_jd`

### 用户价值与必要性

将一份真实简历与一个具体岗位 JD 建立“要求—证据—缺口”矩阵，避免模型只根据城市、
行业或宽泛岗位名称生成泛化结论。Prompt 可以撰写分析，但不能独立证明读取的是哪个简历
版本、哪个 JD，也不能稳定保留证据指纹和缺口状态。

### 输入 Schema

```json
{
  "type": "object",
  "properties": {
    "resume_path": {"type": "string", "minLength": 1},
    "job_id": {"type": "string", "minLength": 1, "maxLength": 80},
    "job_description": {"type": "string", "minLength": 1, "maxLength": 50000},
    "target_role": {"type": "string", "maxLength": 200}
  },
  "required": ["resume_path"],
  "oneOf": [
    {"required": ["job_id"]},
    {"required": ["job_description"]}
  ],
  "additionalProperties": false
}
```

`job_id` 与 `job_description` 必须二选一。`job_id` 只解析已配置数据目录下 `jobs/*.json`
中的精确记录；不能把搜索关键词、城市或职位类别当成完整 JD。

### 输出 Schema

```json
{
  "type": "object",
  "required": ["resume", "job", "method", "coverage_score", "summary", "comparisons", "gaps"],
  "properties": {
    "resume": {"type": "object", "required": ["path", "sha256"]},
    "job": {"type": "object", "required": ["fingerprint", "source"]},
    "method": {"const": "deterministic_concept_evidence_v1"},
    "coverage_score": {"type": "integer", "minimum": 0, "maximum": 100},
    "requires_human_review": {"const": true},
    "summary": {"type": "object"},
    "comparisons": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["criterion_type", "criterion", "status", "evidence"],
        "properties": {
          "status": {"type": "string", "enum": ["matched", "partial", "gap"]},
          "evidence": {"type": "array"}
        }
      }
    },
    "gaps": {"type": "array"},
    "risk_warnings": {"type": "array"}
  }
}
```

- 数据源：配置目录中的简历文件，以及 `jobs/*.json` 精确岗位记录或用户本轮提供的完整 JD。
- 风险等级：`read`；不修改简历或岗位文件。
- 幂等性：简历 SHA-256、JD fingerprint 和匹配算法版本不变时幂等。
- 超时：本地比较 10 秒。
- 错误码：`missing_job_description`、`ambiguous_job_source`、`invalid_job_id`、
  `job_not_found`、`job_parse_failed`、`invalid_job_description`、
  `job_description_too_large`、以及 `read_resume` 的文件错误码。
- 脱敏：日志和 metadata 只记录相对路径、简历 SHA-256、job ID、JD fingerprint、条件数和
  coverage score；不得记录简历正文、JD 全文或真实个人信息。
- 评分边界：coverage score 是可解释的概念证据覆盖率，不是录用概率，必须返回
  `requires_human_review=true`；没有简历原文证据的要求只能标记为 gap。
- 验收：pytest 覆盖精确 job_id、用户提供 JD、缺少/重复岗位来源、未知岗位、证据引用、
  gap、输入文件不被修改和英文子串误匹配；人工确认每条 evidence 均来自指定简历原文。

## Contract 3：`draft_resume_patch`

### 用户价值与必要性

根据真实简历和 JD 生成可审阅的结构化补丁，而不是直接覆盖原文件。Prompt 可以提出
文字建议，但不能可靠绑定文件版本、定位修改位置或阻止把不存在的经历写入文件。

### 输入 Schema

```json
{
  "type": "object",
  "properties": {
    "document_id": {"type": "string"},
    "base_sha256": {"type": "string"},
    "target_role": {"type": "string", "minLength": 1},
    "job_description": {"type": "string", "minLength": 1, "maxLength": 50000},
    "changes": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["section", "before", "after", "evidence"],
        "properties": {
          "section": {"type": "string"},
          "before": {"type": "string"},
          "after": {"type": "string"},
          "evidence": {"type": "array", "items": {"type": "string"}}
        }
      }
    }
  },
  "required": ["document_id", "base_sha256", "target_role", "job_description", "changes"],
  "additionalProperties": false
}
```

### 输出 Schema

```json
{
  "type": "object",
  "required": ["patch_id", "base_sha256", "diff", "claims_review", "requires_approval"],
  "properties": {
    "patch_id": {"type": "string"},
    "base_sha256": {"type": "string"},
    "diff": {"type": "string"},
    "claims_review": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "claim": {"type": "string"},
          "evidence": {"type": "array", "items": {"type": "string"}},
          "status": {"type": "string", "enum": ["supported", "needs_confirmation", "unsupported"]}
        }
      }
    },
    "requires_approval": {"const": true}
  }
}
```

- 数据源：`read_resume` 返回的指定版本、用户提供的 JD 和用户确认过的事实。
- 风险等级：`write`，但只生成补丁，不修改原文件；采用补丁属于人工确认动作。
- 幂等性：相同 base 指纹和规范化输入应生成相同补丁语义；`patch_id` 可不同。
- 超时：20 秒。
- 错误码：`resume_version_conflict`、`missing_evidence`、`unsupported_claim`、
  `invalid_patch`、`document_not_found`、`patch_too_large`。
- 脱敏：日志只记录 document/patch ID、指纹、修改段数和 claims 状态；不记录正文。
- 验收：pytest 覆盖版本冲突、空证据、禁止直接覆盖和 claims 分类；人工逐条检查是否夸大
  经历、产出、用户规模、上线状态或 ownership，未确认时状态必须是
  `waiting_for_approval`。

## Contract 4：`gmail_search` / `qq_mail_search`

两者共享业务 Contract，由不同适配器处理 Gmail API 与 QQ Mail 的授权和协议差异。

### 用户价值与必要性

查找面试通知、岗位往来和 HR 邮件，并保留真实邮件 ID 与时间。Prompt 无法访问邮箱，
也不能把推测当成真实来信。

### 输入 Schema

```json
{
  "type": "object",
  "properties": {
    "query": {"type": "string", "minLength": 1, "maxLength": 500},
    "profile": {"type": "string", "default": "primary"},
    "after": {"type": "string", "format": "date-time"},
    "before": {"type": "string", "format": "date-time"},
    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
    "include_snippet": {"type": "boolean", "default": false}
  },
  "required": ["query"],
  "additionalProperties": false
}
```

### 输出 Schema

```json
{
  "type": "object",
  "required": ["profile", "messages"],
  "properties": {
    "profile": {"type": "string"},
    "account_masked": {"type": "string"},
    "messages": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["message_id", "thread_id", "subject", "from_masked", "received_at"],
        "properties": {
          "message_id": {"type": "string"},
          "thread_id": {"type": "string"},
          "subject": {"type": "string"},
          "from_masked": {"type": "string"},
          "received_at": {"type": "string", "format": "date-time"},
          "snippet": {"type": ["string", "null"]},
          "has_attachments": {"type": "boolean"}
        }
      }
    }
  }
}
```

- 数据源：Gmail API；QQ Mail 优先 OAuth/官方接口，若使用 IMAP 则必须使用授权码，
  不保存登录密码。
- 风险等级：`read`。
- 幂等性：同一邮箱状态和查询下近似幂等；新邮件到达会改变结果。
- 超时：15 秒。
- 错误码：`missing_credentials`、`authentication_failed`、`insufficient_scope`、
  `mailbox_unavailable`、`rate_limited`、`query_invalid`、`provider_timeout`。
- 凭据：YAML 保存 profile 到环境变量名的映射；Gmail refresh token/client secret、QQ
  OAuth token 或 IMAP 授权码放 `.env`、系统环境变量或 secret manager。结果只允许
  暴露 profile、脱敏账号、scope、过期时间、邮件 ID 和完成任务所需的最小邮件字段。
- 脱敏：默认不返回正文，snippet 需显式请求；日志不得记录主题、发件人原文、正文、
  附件名、查询全文或 token。
- 验收：pytest 使用假邮箱适配器验证分页、scope、脱敏和错误映射；人工使用测试邮箱
  确认只读、不改变已读状态、不下载附件，日志无邮件隐私。

## Contract 5：`gmail_create_draft` / `qq_mail_create_draft`

### 用户价值与必要性

把用户审阅过的求职邮件保存为邮箱草稿，避免复制错误。Prompt 只能生成文字，不能证明
草稿已在正确账号创建，也不能约束邮箱副作用。

### 输入 Schema

```json
{
  "type": "object",
  "properties": {
    "profile": {"type": "string", "default": "primary"},
    "to": {"type": "array", "items": {"type": "string", "format": "email"}, "minItems": 1},
    "cc": {"type": "array", "items": {"type": "string", "format": "email"}},
    "subject": {"type": "string", "minLength": 1, "maxLength": 200},
    "body_text": {"type": "string", "minLength": 1, "maxLength": 50000},
    "attachment_document_ids": {"type": "array", "items": {"type": "string"}},
    "idempotency_key": {"type": "string", "minLength": 8}
  },
  "required": ["to", "subject", "body_text", "idempotency_key"],
  "additionalProperties": false
}
```

### 输出 Schema

```json
{
  "type": "object",
  "required": ["draft_id", "profile", "to_masked", "subject", "content_sha256", "created_at"],
  "properties": {
    "draft_id": {"type": "string"},
    "profile": {"type": "string"},
    "account_masked": {"type": "string"},
    "to_masked": {"type": "array", "items": {"type": "string"}},
    "subject": {"type": "string"},
    "content_sha256": {"type": "string"},
    "attachment_sha256": {"type": "array", "items": {"type": "string"}},
    "created_at": {"type": "string", "format": "date-time"},
    "sent": {"const": false}
  }
}
```

- 数据源：Gmail Drafts API 或 QQ Mail 支持的草稿接口/受控 IMAP Drafts 文件夹。
- 风险等级：`write`；只创建草稿，绝不发送。
- 幂等性：必须使用 `idempotency_key`；重复请求返回原 draft ID，不重复创建。
- 超时：20 秒；超时后先按 idempotency key 查询状态，不能盲目重试。
- 错误码：`missing_credentials`、`authentication_failed`、`invalid_recipient`、
  `attachment_not_found`、`attachment_changed`、`draft_conflict`、
  `insufficient_scope`、`provider_timeout`。
- 凭据：与搜索工具共用集中 profile 设置，但需要 draft/write scope；结果可返回 profile、
  脱敏账号/收件人、draft ID 和内容/附件指纹，不返回 token 或密码。
- 脱敏：正文、完整收件人和简历附件内容不进入日志；`display` 只给出脱敏收件人和草稿
  状态。创建前必须展示正文和附件供用户确认，且不能声称“已发送”。
- 验收：pytest 覆盖幂等、附件指纹变化、scope 和超时后状态查询；人工确认草稿出现在
  正确账号、没有发送、收件人与附件正确，日志无正文和简历内容。

## Contract 6（可选）：`send_email`

### 用户价值与必要性

在用户明确确认后发送既有草稿。该动作有真实外部副作用，不能由 Prompt 中笼统的
“可以发送”代替当前草稿级确认。

### 输入 Schema

```json
{
  "type": "object",
  "properties": {
    "provider": {"type": "string", "enum": ["gmail", "qq_mail"]},
    "profile": {"type": "string"},
    "draft_id": {"type": "string"},
    "expected_content_sha256": {"type": "string"},
    "approval_token": {"type": "string"},
    "idempotency_key": {"type": "string"}
  },
  "required": ["provider", "profile", "draft_id", "expected_content_sha256", "approval_token", "idempotency_key"],
  "additionalProperties": false
}
```

### 输出 Schema

```json
{
  "type": "object",
  "required": ["message_id", "thread_id", "sent_at", "profile", "recipient_count"],
  "properties": {
    "message_id": {"type": "string"},
    "thread_id": {"type": "string"},
    "sent_at": {"type": "string", "format": "date-time"},
    "profile": {"type": "string"},
    "recipient_count": {"type": "integer"},
    "content_sha256": {"type": "string"}
  }
}
```

- 数据源：已存在且内容指纹匹配的邮箱草稿。
- 风险等级：`external`，必须人工确认；默认不在 `allow_risk_levels` 中启用。
- 幂等性：以 provider、draft ID 和 idempotency key 保证；超时后先查询已发送状态。
- 超时：20 秒。
- 错误码：`approval_required`、`approval_expired`、`draft_not_found`、
  `draft_changed`、`invalid_recipient`、`authentication_failed`、`send_rejected`、
  `send_status_unknown`、`provider_timeout`。
- 凭据：集中 profile 提供 send scope；结果只暴露 profile、外部 message/thread ID、发送
  时间、人数和内容指纹。日志不得记录收件人原文、主题、正文、附件或凭据。
- 验收：pytest 覆盖无确认、过期确认、草稿变化、重复提交和超时状态未知；人工必须在
  测试邮箱逐项核对确认页面与实际邮件，并验证未经确认绝不发送。

## 注册与运行建议

实现后的工具由 Registry 显式注册，再由 YAML 启用；敏感能力默认关闭：

```yaml
tools:
  enabled:
    - search_jobs_serpapi
    - read_resume
    - draft_resume_patch
    - gmail_search
    - gmail_create_draft
  allow_risk_levels:
    - read
    - write
```

`send_email` 即使注册，也不应默认出现在 `enabled` 或允许的风险等级中。Runtime 在执行前
仍需校验 JSON Schema、权限、超时和结果大小，并将错误作为合法 `ToolResult` 返回模型，
而不是让工具异常中断整个回合。

## 可交给 Coding Agent 生成

- 基于上述 Contract 生成 Tool 类、provider adapter 和 Registry 注册代码。
- 实现 settings 中的凭据 profile 解析、环境变量覆盖和脱敏 metadata。
- 生成 Pydantic 输入/输出模型及对应 JSON Schema。
- 生成 pytest 骨架、fake adapter、HTTP/邮箱响应 fixture 和错误映射测试。
- 生成 README 配置样例、`.env.example` 变量名和本地测试命令。

Coding Agent 生成的代码不得自行填入真实 key、真实邮箱密码、真实简历或生产收件人。

## 必须人工验收

- 对照原始简历逐条检查是否夸大、捏造经历、量化成果、上线状态或用户规模。
- 验证未获得当前草稿级明确确认时，系统绝不会发送邮件或声称已经发送。
- 验证每条岗位搜索结果保留来源 URL、来源名称、查询时间和可用的发布时间。
- 使用测试凭据验证 SerpAPI primary / backup profile 均可切换，且环境变量覆盖符合预期。
- 检查应用日志、ToolResult、测试输出、截图和验收记录中没有真实 API key、OAuth token、
  授权码或账号密码。
- 检查邮件正文、主题、收件人、附件和简历隐私没有进入日志或非必要的长期存储。

任一人工验收项失败时不得将对应工具标记为可生产使用；涉及关键经历改写时保持
`waiting_for_approval`，涉及缺失材料时保持 `waiting_for_user`，无法可靠完成时进入
`failed`。
