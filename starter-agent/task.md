# 求职邮件工具套装实现任务

## Task1：建立邮件领域契约与安全配置模型

### 任务目标

建立邮件工具套装共用的领域模型、adapter 协议、统一错误类型和配置解析，为后续功能提供稳定边界，同时保证仓库配置中不出现真实凭据。

### 子任务

1. 新增邮件模块包及 `models`、`errors`、adapter protocol 等基础模块。
2. 定义搜索条件、邮件摘要、邮件详情、附件元数据、草稿、审批、发送回执、capability 和结果完整性模型。
3. 定义统一的 `EmailError` 及设计文档列出的稳定错误码。
4. 在 settings 中新增 email 配置模型，支持：
   - active profile。
   - `mock_fixture` 与 `imap_smtp` adapter 类型。
   - Gmail、QQ、自定义邮箱类型标识。
   - IMAP/SMTP host、port、SSL/TLS/STARTTLS 配置。
   - OAuth、应用专用密码、QQ 授权码等认证方式标识。
   - 仅保存环境变量名称的账号与凭据引用。
   - 默认关闭 `real_send_enabled`。
5. 实现配置合法性校验，包括未知 adapter、缺失连接字段、非法端口、fixture 路径越界和冲突 profile。
6. 新增配置模型与错误码单元测试。

### 依赖关系

- 依赖已确认的 `docs/requirements.md` 和 `design.md`。
- 不依赖其他实现 Task。
- Task2 及后续 Task 使用本 Task 定义的模型、协议和配置。

### 验收标准

- 合法 mock profile 能通过 settings 解析。
- 合法 IMAP/SMTP profile 只通过环境变量名引用凭据，不读取或输出凭据值。
- 非法 adapter、端口、传输方式和越界 fixture 路径返回确定的配置错误。
- `real_send_enabled` 未配置时为 `false`。
- 统一错误码可被序列化为不含原始 provider 异常和 secret 的结构。
- Task1 新增的定向测试全部通过，原有 settings 测试无回归。

### 预估复杂度

中等。主要复杂度来自配置组合校验和后续兼容性设计。

## Task2：实现 EmailStore、幂等记录与草稿级审批基础设施

### 任务目标

实现本地草稿、opaque 引用、幂等记录、审批记录和发送回执的持久化边界，为草稿与发送门禁提供可信的服务端事实。

### 子任务

1. 定义 `EmailStore` 接口和 SQLite 实现。
2. 增加草稿、审批、幂等操作和发送回执所需的数据表或等价持久化模型。
3. 实现 opaque `message_ref`、`thread_ref`、`source_ref`、`draft_id`、`approval_id` 的创建和解析。
4. 将引用绑定到 session、profile、对象类型和有效期，拒绝跨 session、跨 profile 或过期引用。
5. 实现草稿组合指纹：
   - 收件人、抄送、密送。
   - 主题和正文。
   - 回复目标。
   - 附件引用及 SHA-256。
6. 实现幂等键哈希存储；同键同请求返回既有结果，同键不同请求返回冲突。
7. 实现审批生命周期、过期、撤销、消费和草稿变化失效逻辑。
8. 新增存储隔离、指纹、幂等和审批单元测试。

### 依赖关系

- 依赖 Task1 的领域模型和错误类型。
- Task5、Task6 和 Task8 依赖本 Task 的 store 与审批能力。

### 验收标准

- 草稿、审批和发送回执能够保存并按 opaque ID 查询。
- 引用不能被当作任意本地路径或 provider UID 使用。
- 跨 session/profile 的引用访问被稳定拒绝。
- 草稿任一关键字段变化都会改变组合指纹并使旧审批失效。
- 同一幂等请求不会创建重复草稿或重复发送记录。
- store 的普通查询结果和日志不包含凭据。
- Task2 定向测试全部通过。

### 预估复杂度

高。审批绑定、幂等和发送状态一致性是整个套装的核心安全基础。

## Task3：实现 Mock Fixture Adapter 与测试数据

### 任务目标

提供与真实邮箱 adapter 相同契约的 mock 实现，使搜索、读取、草稿和模拟发送流程可以在无外部账号依赖的情况下稳定验收。

### 子任务

1. 新增脱敏 fixture 目录和数据 Schema。
2. 准备中文 HR 邮件、英文面试邀请、多封线程、Offer 邮件、普通噪声邮件和长正文样本。
3. 实现 fixture 加载、结构化搜索、分页和稳定排序。
4. 实现无副作用读取、线程读取、纯文本正文和附件元数据返回。
5. 实现隔离的 mock 草稿保存，不修改只读 fixture。
6. 实现 mock 模拟发送，只返回：
   - `delivery_mode="mock"`。
   - `status="simulated_sent"`。
   - `external_delivery=false`。
7. 增加认证失败、限流、超时、无效 cursor、草稿冲突和发送结果未知等错误 fixture。
8. 新增 adapter contract 单元测试。

### 依赖关系

- 依赖 Task1 的 adapter 协议、领域模型和错误码。
- 依赖 Task2 的 mock 草稿及幂等存储接口。
- Task4 至 Task6 使用本 Task 作为默认 adapter。

### 验收标准

- 固定查询可以命中预期 HR 或面试邀请邮件。
- 分页结果顺序稳定，cursor 与 profile、查询指纹绑定。
- `read` 不改变 fixture 的未读标记。
- 创建 mock 草稿不会产生任何发送回执。
- 模拟发送不会发起网络连接，也不会报告真实外部发送。
- 错误 fixture 能稳定映射到统一错误类型。
- fixture 中不包含真实邮箱、招聘联系人或真实凭据。
- Task3 定向测试全部通过。

### 预估复杂度

中等。重点是 fixture 覆盖面、契约一致性和完全隔离外部网络。

## Task4：实现 EmailManager 的 provider 管理、搜索、读取与结果裁剪

### 任务目标

新增共享 `EmailManager`，实现 profile/provider 选择、adapter 调用、统一错误映射，以及 `email_search`、`email_read` 所需的领域感知裁剪和来源追踪。

### 子任务

1. 实现 `EmailManager` 的 profile 解析、adapter factory/cache 和 capability 校验。
2. 实现结构化搜索参数校验，拒绝无条件遍历整个邮箱。
3. 实现搜索分页、相关性/时间排序和 opaque cursor。
4. 实现无副作用读取，要求 adapter 使用 PEEK 语义。
5. 实现 MIME 纯文本规范化接口；HTML 仅转换为安全纯文本，不加载远程资源。
6. 实现邮件专项结果裁剪：
   - 搜索结果数量裁剪。
   - 正文与线程裁剪。
   - 始终返回 `is_truncated`、`has_more`、`source_ref`。
   - 裁剪后保持合法 JSON。
7. 与现有 `ToolResultGuard` 协同，保证 Runtime 二次裁剪后仍可追溯。
8. 实现统一错误码、`retryable` 和安全 `display` 映射。
9. 新增 Manager 搜索、读取、裁剪、profile 切换和错误映射测试。

### 依赖关系

- 依赖 Task1 的领域模型、配置和错误码。
- 依赖 Task2 的 opaque 引用与 source store。
- 依赖 Task3 的 mock adapter。
- Task5 至 Task8 复用本 Task 的 Manager。

### 验收标准

- mock 搜索命中预期 HR 邮件，并返回可继续读取的 `message_ref`。
- 读取面试邀请能返回邮件头、纯文本正文、线程关系、附件元数据和来源引用。
- 长正文发生裁剪时 `is_truncated=true`、`has_more=true` 且 `source_ref` 非空。
- 未裁剪结果也显式返回完整性字段。
- Runtime 再次裁剪时结果仍为合法 JSON，并可通过引用回查。
- 缺少 profile、能力不支持、消息不存在和 provider 超时均返回稳定错误码。
- 普通日志不包含查询全文、主题、地址、snippet 或正文。
- Task4 定向测试及现有 Context 裁剪测试全部通过。

### 预估复杂度

高。搜索/读取结果既要适合模型使用，又要满足隐私、分页和 Context 预算约束。

## Task5：实现草稿创建、附件校验与发送前确定性检查

### 任务目标

通过 `EmailManager` 创建本地、mock 或邮箱草稿，明确保证“草稿不等于发送”，并阻止带有确定性严重错误的草稿进入发送流程。

### 子任务

1. 实现收件人、抄送、密送的格式、数量和重复校验。
2. 实现主题、正文、回复目标及残留占位符检查。
3. 实现附件引用解析、允许目录校验、存在性、大小及 SHA-256 校验。
4. 实现 `local`、`mock`、`mailbox` 三种 `storage_scope`，禁止静默降级。
5. 实现草稿组合指纹和版本更新规则。
6. 实现草稿创建幂等；超时后按幂等键查询，不盲目重复创建。
7. 保证结果包含 `status="draft_only"`、`sent=false`、`external_delivery=false` 和 `requires_approval_to_send=true`。
8. 保证用户可读结果明确说明“草稿已创建，邮件尚未发送”。
9. 新增草稿、附件、占位符、幂等和存储范围测试。

### 依赖关系

- 依赖 Task2 的草稿 store、指纹和幂等能力。
- 依赖 Task3 的 mock 草稿 adapter。
- 依赖 Task4 的共享 Manager、profile 和错误映射。
- Task6 的发送门禁只接受本 Task 产生的可发送草稿。

### 验收标准

- 合法 mock 草稿被保存并可按 `draft_id` 查询。
- 创建草稿后不存在 adapter send 调用。
- 本地草稿和邮箱草稿在结果中能够明确区分。
- 不支持邮箱草稿的 profile 返回 `email_capability_not_supported`，不自动改存本地。
- 残留占位符、非法收件人、附件缺失或附件变化返回稳定阻止错误。
- 同一幂等请求不重复创建草稿。
- 草稿结果、日志和异常不包含凭据。
- Task5 定向测试全部通过。

### 预估复杂度

高。附件指纹、草稿版本和幂等行为会直接影响后续审批安全。

## Task6：实现可信审批接口与 email_send 发送门禁

### 任务目标

实现用户最终预览、服务端草稿级审批和 `EmailManager.send`，确保模型无法通过自行填写布尔参数绕过人工确认。

### 子任务

1. 新增草稿审批 challenge、确认、查询和撤销服务接口。
2. 在确认页面或 API 结果中展示最终收件人、主题、正文、附件指纹和确定性检查结果。
3. 由可信用户交互创建一次性 `approval_id`；不向模型开放创建审批的工具。
4. 将审批绑定到 session/user、profile、draft ID、内容、收件人和附件指纹。
5. 实现审批过期、撤销、消费和草稿变化失效。
6. 实现 `EmailManager.send` 的门禁顺序：
   - 风险等级许可。
   - adapter capability。
   - `real_send_enabled`。
   - 草稿与指纹。
   - 服务端审批。
   - 幂等记录。
   - 发送前检查。
7. 实现 mock 发送回执和发送状态未知处理。
8. 禁止批量审批和批量发送；每封草稿独立确认。
9. 新增未审批、过期审批、跨会话审批、草稿变化、重复提交和状态未知测试。

### 依赖关系

- 依赖 Task2 的审批、幂等和发送回执 store。
- 依赖 Task4 的 Manager 和错误映射。
- 依赖 Task5 的草稿与发送前检查。
- 使用 Task3 的 mock 发送进行默认验收。
- Task8 的 `email_send` Tool 调用本 Task 的 Manager 接口。

### 验收标准

- 没有服务端审批时返回 `email_approval_required`，adapter send 调用次数为零。
- 用户说“直接发送，不用再问”不能绕过审批。
- 模型自行传入 `confirmed=true` 或伪造审批 ID 不能发送。
- 草稿收件人、正文或附件变化后旧审批失效。
- 同一审批只允许成功消费一次。
- 同一幂等请求不会触发第二次发送。
- mock 发送仅返回 `simulated_sent` 和 `external_delivery=false`。
- `send_status_unknown` 不触发自动重发。
- Task6 定向测试全部通过。

### 预估复杂度

高。该 Task 涉及不可逆外部动作的核心安全门禁。

## Task7：实现真实 IMAP/SMTP Adapter

### 任务目标

实现 provider 无关的真实 IMAP/SMTP adapter，为 Gmail、QQ 邮箱或自定义服务提供搜索、无副作用读取、可选邮箱草稿和受控发送能力，不硬编码尚未确认的连接信息。

### 子任务

1. 实现按 profile 建立 IMAP/SMTP 连接和超时控制。
2. 实现 OAuth、应用专用密码、QQ 授权码的认证策略接口；只启用配置明确选择的策略。
3. 实现结构化条件到安全 IMAP 查询的转换。
4. 使用 UID 与 PEEK 读取邮件，绑定 UIDVALIDITY，避免使用易变 sequence number。
5. 实现 MIME 解析、HTML 安全转纯文本和附件元数据提取。
6. 探测 Drafts mailbox 与 APPEND capability；不支持时返回统一错误。
7. 实现 SMTP 发送、幂等记录、明确失败和状态未知处理。
8. 禁止连接跨 profile 复用，关闭底层协议 debug 日志。
9. 使用 fake IMAP/SMTP client 新增协议、认证、错误映射、超时和无重复发送测试。

### 依赖关系

- 依赖 Task1 的配置、认证标识和 adapter 协议。
- 依赖 Task2 的幂等与发送回执。
- 依赖 Task4 的 Manager 错误映射和引用规则。
- 依赖 Task5、Task6 的草稿和审批门禁。
- Task8 通过统一 Manager 暴露该 adapter。

### 验收标准

- fake IMAP 搜索和读取符合与 mock adapter 相同的 contract。
- 读取操作使用 PEEK，不改变邮件已读标记。
- host、port、传输方式或认证配置缺失时返回稳定配置/凭据错误，不猜测默认值。
- 缺少凭据时返回 `email_missing_credentials`，且不建立网络连接。
- 邮箱不支持 Drafts 时返回 capability 错误，不静默创建其他类型草稿。
- SMTP 调用前必须通过 Task6 的审批门禁。
- SMTP 超时不能导致自动重复发送。
- adapter、日志、异常和测试输出中没有真实凭据。
- Task7 fake client 测试全部通过。

### 预估复杂度

高。IMAP/SMTP 协议差异、认证和发送结果不确定性需要谨慎处理。

## Task8：实现四个 Tool 契约并接入 ToolRegistry 与 Runtime

### 任务目标

将 `EmailManager` 通过四个稳定的模型可见工具暴露，并完成注册、风险等级、配置启用和 API 工具列表集成。

### 子任务

1. 实现 `email_search` Tool：
   - 风险等级 `read`。
   - 使用设计文档中的结构化搜索 Schema。
2. 实现 `email_read` Tool：
   - 风险等级 `read`。
   - 强制无副作用读取。
3. 实现 `email_create_draft` Tool：
   - 风险等级 `write`。
   - 只创建草稿。
4. 实现 `email_send` Tool：
   - 风险等级 `external`。
   - 要求 `draft_id`、内容指纹、`approval_id` 和幂等键。
5. 四个工具只做参数转换和 `ToolResult` 封装，不直接连接 provider。
6. 修改 `ToolRegistry`，构建单一共享 `EmailManager` 并注入四个工具。
7. 修改 settings/示例配置，使 mock 搜索、读取和草稿可显式启用。
8. 保持 `email_send` 与 `external` 默认不启用。
9. 更新 `/v1/tools` 集成测试、注册测试和强制工具调用测试。

### 依赖关系

- 依赖 Task4 至 Task7 提供的完整 Manager、mock 和真实 adapter 能力。
- Task9 在本 Task 的工具与 Runtime 集成基础上强化隐私治理。
- Task10 使用本 Task 的公开工具执行端到端验收。

### 验收标准

- `/v1/tools` 返回四个工具的正确名称、描述和风险等级。
- 四个 Tool Schema 拒绝额外字段、错误类型和越界参数。
- 四个 Tool 实例共享同一个 `EmailManager`。
- 未在 `tools.enabled` 中配置时，邮件工具不会被注册。
- `email_send` 未允许 `external` 时被现有 `ToolPolicy` 拒绝。
- 即使允许 `external`，缺少草稿级审批仍被 Manager 拒绝。
- 工具成功和失败均返回合法 `ToolResult`。
- Task8 定向测试及现有 Registry/API 测试全部通过。

### 预估复杂度

中等。主要工作是契约准确性、共享依赖构造和现有 Runtime 兼容。

## Task9：完善隐私日志、Context 治理与安全配置说明

### 任务目标

确保邮件工具套装在日志、Context、配置和错误路径中不泄露凭据或不必要的邮件隐私，并提供不含真实密钥的环境变量示例。

### 子任务

1. 增加邮件专用安全日志字段白名单。
2. 屏蔽 IMAP/SMTP 和底层网络库的 debug/INFO 敏感输出。
3. 确保日志不记录：
   - 查询全文。
   - 邮件地址原文。
   - 主题、snippet、正文。
   - 附件名及内容。
   - 密码、授权码、OAuth token、API key。
4. 让 EmailManager 的 `source_ref` 与 Runtime 的 `raw_source_ref` 协同工作。
5. 确保邮件正文不自动进入长期记忆。
6. 更新 `.env.example`，只增加非敏感变量名称和注释，不写任何真实值。
7. 更新 `config/config.example.yaml` 的 mock 示例和真实 profile 字段说明，真实发送保持关闭。
8. 增加带唯一敏感标记的日志、ToolResult、异常和测试输出扫描。

### 依赖关系

- 依赖 Task4 的裁剪与来源引用。
- 依赖 Task7 的协议客户端。
- 依赖 Task8 的 Tool、Registry 和 Runtime 集成。
- Task10 的完整验收以本 Task 的隐私防护为前提。

### 验收标准

- 唯一测试凭据和完整邮件隐私标记在日志、ToolResult、异常及 pytest 输出中零命中。
- `.env.example` 和示例 YAML 不包含真实账号、密码、授权码、Token 或 API key。
- 长邮件经两层裁剪后保持合法 JSON、完整性元数据和来源引用。
- 邮件正文不会被自动写入长期记忆。
- 默认配置不注册或不允许真实发送。
- Task9 安全测试及现有 logging/context/memory 测试全部通过。

### 预估复杂度

中等偏高。需要覆盖正常、失败、超时和二次裁剪等多条泄露路径。

## Task10：补齐端到端测试、真实受控验收与文档

### 任务目标

完成邮件工具套装的自动化回归、受控真实 IMAP/SMTP 验收以及用户可复核的工具与验收文档。

### 子任务

1. 新增 mock 端到端用例：
   - 搜索命中 HR/面试邀请。
   - 读取邮件与线程。
   - 长正文裁剪并带完整性元数据。
   - 总结所需信息进入 Agent Context。
   - 创建草稿但不发送。
   - 未审批发送被拒绝。
   - 审批后执行 mock 模拟发送。
2. 新增配置缺失用例：
   - profile 缺失。
   - host/port/传输方式缺失。
   - 凭据环境变量缺失。
   - capability 不支持。
   - 真实发送开关关闭。
3. 新增失败与幂等用例：
   - provider 超时。
   - 草稿冲突。
   - 审批失效。
   - SMTP 结果未知。
   - 重复提交不重复发送。
4. 在 mock 自动化测试全部通过后，按设计执行受控真实验收：
   - 使用 `.env` 中已授权的 IMAP/SMTP 验收凭据。
   - 不打印或复制凭据值。
   - 使用确认后的专用测试收件人和测试内容。
   - 每封真实测试邮件发送前单独展示最终版本并取得人工确认。
   - 核对读取不改变已读标记、草稿不进入 Sent、发送回执与测试邮箱一致。
5. 更新或新增邮件工具说明文档，记录：
   - 工具契约和风险等级。
   - mock 与真实 profile 配置方法。
   - 草稿和发送审批边界。
   - 稳定错误码。
   - 隐私与凭据规则。
6. 更新验收文档，记录自动化测试命令、测试结果、真实验收证据的非敏感摘要和未覆盖项。
7. 运行完整测试套件并处理回归。

### 依赖关系

- 依赖 Task1 至 Task9。
- 真实账号验收还依赖用户确认邮箱类型、host、port、SSL/TLS、认证方式、环境变量名映射和专用测试收件人。
- 如果真实连接参数仍不完整，必须完成全部 mock 验收和缺配置安全失败验收，并将真实验收阻塞原因写入验收文档，不能硬编配置。

### 验收标准

- 指定的五类核心测试全部有自动化覆盖：
  - mock 搜索命中。
  - 读取长正文并正确裁剪。
  - 创建草稿不会发送。
  - `email_send` 未批准时拒绝。
  - 配置缺失返回稳定错误码。
- mock 端到端流程通过，且没有外部网络副作用。
- 真实验收只在参数完整、测试收件人明确和逐封确认后执行。
- 真实收取结果可与测试邮箱核对；真实发送结果以 SMTP/邮箱回执和测试收件箱为准，不以模型文本为准。
- 日志和测试产物通过凭据与隐私扫描。
- 完整测试套件通过，或验收文档逐项记录与邮件套装无关的既有失败。
- 工具文档、配置说明和验收记录与实际行为一致。

### 预估复杂度

高。涉及跨模块回归、真实邮箱副作用控制和人工验收证据整理。
