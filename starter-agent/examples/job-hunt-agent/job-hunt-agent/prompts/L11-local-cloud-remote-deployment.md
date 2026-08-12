# L11 · 本机 / 云端远程访问部署

用途：在正式产品化前，审计项目并完成 Docker、Cloudflare Named Tunnel、Cloudflare Access 与 GitHub 部署链路；支持“本机作为服务器”和“云服务器”两种模式。

使用前填写能确定的字段；Docker 服务名、容器端口、启动命令等能够从仓库确认的内容可以留空，由 Coding Agent 审计后补全。

---BEGIN---
你是我的部署工程协作伙伴。请使用中文工作。

我要把现有 Agent 项目部署为可以通过真实 HTTPS 域名访问的服务。部署目标支持两种模式：

- 本机模式：Mac 或 Linux 本机运行 Docker Compose，通过 Cloudflare Named Tunnel 提供远程访问。
- 云端模式：远程 Linux 云服务器运行 Docker Compose，通过 Cloudflare Named Tunnel 提供远程访问。

请先读取仓库中的 `DEPLOYMENT.md`；如果不存在则创建。它必须持续记录“已确认信息、待确认或待配置、安全约定、执行计划、验收结果”，但不得记录任何真实 Token、API Key、邮箱密码、SSH 私钥或其他敏感值。

## 部署输入

项目路径或仓库：
分支：
部署模式：本机 / 云服务器
目标域名：
Docker 服务名：
容器端口：
目标运行架构：自动识别 / linux/amd64 / linux/arm64
镜像来源：部署机器本地构建 / GitHub Container Registry / 其他
域名是否已接入 Cloudflare：是 / 否
是否需要 Cloudflare Access：是 / 否
Cloudflare Access 允许访问的邮箱或身份范围：
是否要求开机自动启动：是 / 否
GitHub 目标仓库：
是否需要 GitHub 自动部署：是 / 否
生产环境变量文件路径：

仅云端模式填写：
服务器地址：
SSH 端口：
SSH 用户：
SSH 私钥路径：
是否有 sudo 权限：是 / 否

其他环境变量或特殊要求：

## 第一阶段：审计与部署计划

先审查真实项目，不要根据输入中的猜测直接修改配置：

1. 检查工作区状态、当前分支、Git remote、未提交修改和目标仓库；不得覆盖现有修改，不得执行 force push、hard reset 或擅自切换 remote。当前 `origin` 与目标仓库不一致时，记录差异、影响和建议，等待确认。
2. 检查真实启动命令、监听地址、Web/API 入口、前后端关系、流式响应方式、健康检查、Dockerfile、Compose、数据库、迁移、Volume、上传文件、RAG、Trace、日志和 MCP/Browser 运行要求。
   - 检查后台任务是否与 Web 请求运行在同一进程，是否已有 Worker、Queue、Task Store 或 Scheduler；根据真实代码决定使用单服务还是 Web + Worker，不凭空引入 Redis、Celery 或新的任务框架。
   - 检查 Router、Task Manager、Parent/Child Run、Task Event、Join 与任务状态实际保存在哪里；确认容器重启后哪些状态可恢复、哪些只能标记为 interrupted/failed，不得把普通进程重启描述成 Checkpoint 恢复。
3. 从代码和配置确认 Docker 服务名、容器端口、监听地址与内部访问地址。容器内 Web 服务必须监听 `0.0.0.0`，但生产 Compose 默认不向公网发布应用、数据库、队列或调试端口，只允许 `cloudflared` 通过 Docker 私有网络访问应用。不要要求用户重复填写仓库可以确定的信息；无法可靠推断时才提问。
4. 检查 `.env.example`、`.gitignore`、前端构建变量和运行日志，确认真实 Secret 不会进入 Git、镜像、前端产物、Trace 或部署文档。
5. 根据部署模式检查运行环境和目标 CPU 架构：
   - 本机模式：Docker Desktop或 Docker Engine、休眠影响、本机磁盘、自动启动条件和 Cloudflare Tunnel 容器运行方式。
   - 云端模式：服务器系统与 `linux/amd64` 或 `linux/arm64` 架构、CPU/内存/磁盘、Docker/Compose、出站网络、SSH、安全组、防火墙和持久化目录。
   - 如果本机与服务器架构不同，明确选择在目标机器构建、使用 Buildx 构建目标架构，或从受控 Registry 拉取对应镜像；不得默认复制不兼容的本机镜像。
6. 检查域名是否真正由 Cloudflare 管理、Zone 是否 Active，以及是否具备创建 Named Tunnel、DNS Route 和 Access Policy 的必要权限。生产或稳定演示禁止使用随机 Quick Tunnel。
7. 检查 GitHub 仓库、目标分支和自动部署条件。区分本机模式的 Self-hosted Runner / 手动部署与云端模式的 GitHub Actions SSH 部署，不默认选择高权限方案。
8. 将确认结果和缺口写入 `DEPLOYMENT.md`。如果存在阻塞，只提出最多 5 个必须由用户回答或亲自完成的问题，优先询问账号验证、域名 Nameserver、Access 身份范围、SSH/Cloudflare 授权和生产 Secret 文件路径。

第一阶段只允许执行只读检查、无副作用的本地验证，以及创建或更新 `DEPLOYMENT.md`。输出部署拓扑、拟修改文件、外部操作、风险、回滚方案和验收清单后停止，等待我回复“确认部署，开始执行”。

## 第二阶段：确认后执行

收到“确认部署，开始执行”后，按照已确认计划持续完成并验证：

1. 在不破坏现有开发环境的前提下，创建或完善生产 Dockerfile、Compose、`.dockerignore`、`.env.example`、健康检查、数据库迁移、日志、资源限制和持久化 Volume。
   - Dockerfile 使用明确版本的基础镜像和项目现有 lockfile；只安装生产依赖，排除 Git、测试缓存、本地数据库、真实 `.env` 与无关构建文件。
   - 在不破坏依赖运行的前提下使用非 root 用户；正确处理 SIGTERM、优雅停止和僵尸进程，避免容器停止时丢失正在写入的 Task Event 或 Trace。
   - 前端需要构建时优先使用多阶段构建；运行镜像不得保留包管理缓存、编译工具和源 Secret。
   - 如果 Browser MCP / Playwright 需要浏览器依赖，明确使用兼容镜像或安装方式，并真实运行一次容器内 Browser 调用，不能只验证模块能够 import。
2. Compose 必须根据真实架构定义清楚服务责任：
   - 至少包含真实 Web/API 服务与 `cloudflared`；存在独立后台执行入口时增加 Worker 服务，并尽量复用同一应用镜像、使用不同启动命令。
   - 数据库、Queue、Task Store 只在真实实现需要时加入；已有外部托管服务时不得再启动冲突的本地副本。
   - 为 Web、Worker、数据库或 Queue 配置真实 healthcheck；服务依赖使用健康状态或应用级重试，不能只依赖容器启动顺序。
   - 使用 `env_file` 或 Secret Store 注入配置，不在 Compose 中写真实值；生产环境不得使用默认密码或示例 Token。
   - 为数据库、上传文件、RAG 索引、Trace、Task State 和其他真实持久数据配置明确 Volume 或外部存储；缓存和临时文件不得误当持久状态。
   - 设置合理的 restart policy、日志轮转、资源限制与服务级停止时间；配置必须适合本机和云端目标，而不是依赖开发机绝对路径。
   - 如果后台任务没有持久 Queue 或 Checkpoint，Worker 重启后必须把无法恢复的任务明确标记为 interrupted/failed，并允许用户安全重试；不得假装从中间步骤恢复。
3. Web/API 服务默认只通过 Docker 内部网络向 `cloudflared` 提供服务；不得把数据库、调试端口或无必要的应用端口直接暴露公网。
4. 使用 Cloudflare remotely-managed Named Tunnel。Tunnel Token 只能通过未提交的环境变量文件、Secret Store 或受限运行环境注入，不得写进 Compose、文档、命令输出或 Git。
5. 将目标域名路由到真实 Docker 内部服务地址，例如 `http://web:8000`；不能用静态成功页、Mock API 或演示页面代替真实 Agent 服务。
6. 如果启用 Cloudflare Access，按确认的邮箱或身份范围创建最小访问策略。先验证未登录被拦截，再验证允许身份能够进入；不得用全员放行规则假装完成 Access。
7. 按模式配置自动启动：
   - 本机模式：只有用户明确要求时才配置 Docker 启动条件或系统启动项；容器使用合适的 restart policy，并说明电脑关机、休眠或 Docker 停止会导致服务下线。
   - 云端模式：启用 Docker 服务、容器 restart policy 和必要的系统启动配置；重启服务器后必须重新验证。
8. 关联 GitHub 时保留现有历史和未提交修改。需要修改 remote、创建 Deploy Key、配置 Self-hosted Runner、GitHub Environment 或 Actions Secrets 时，先展示精确变更并取得确认；不得在公开仓库中让不受信任的 Pull Request 使用生产 Runner 或生产 Secret。
9. 自动部署必须至少执行测试、构建、更新容器和公网健康检查。失败时保留上一可用版本或提供确定的回滚步骤，不得把部署成功仅判断为 Workflow 显示绿色。
10. 生产业务 Secret 保存在部署机器的 `.env.production` 或等价 Secret Store；GitHub 只保存完成部署所必需的最小 Secret。设置合适文件权限，并验证 Git 历史和构建产物中没有真实密钥。
11. 对本地地址、Docker 内部地址和公网域名分别进行真实验证，至少覆盖：
    - `docker compose config` 能解析，镜像能够从干净缓存构建，服务没有使用开发机绝对路径；
    - 容器状态与健康检查；
    - Web 与独立 Worker 的启动、优雅停止、重启策略和日志；
    - HTTPS 和目标域名；
    - Cloudflare Tunnel 连接状态；
    - Cloudflare Access 拦截与允许路径；
    - 页面刷新、API 和流式响应；
    - 一次真实 Agent 对话；
    - 至少一次真实 Tool 或 MCP 调用；
    - 创建一个后台任务，验证 task_id、Worker 执行、Task Event、Join 与最终结果来自真实后端；
    - 重启 Web 容器时后台 Worker 和任务状态符合设计；重启 Worker 时任务能够恢复、重试或明确进入 interrupted/failed，不丢失且不重复副作用；
    - 数据库、上传文件、RAG 索引、Trace 和 Task State 在 `docker compose restart` 后仍然存在；
    - 使用同一份受控环境变量执行 `docker compose down` 再 `up -d` 后，持久数据和服务健康状态符合设计；
    - 开机自动启动要求；
    - GitHub 更新后的部署链路；
    - 日志中没有 Secret。
12. 外部账号验证、付款、域名注册、Nameserver 确认或登录授权必须由用户完成时，明确给出最小操作并等待；完成后从阻塞点继续，不用 Mock、临时静态页或口头说明代替。

完成后更新 `DEPLOYMENT.md`，记录最终拓扑、实际服务名与端口、域名、启动/停止/更新/回滚命令、健康检查、备份恢复、已通过证据、未通过项和剩余风险。不要记录敏感值。

最终报告必须明确区分：

- 已真实完成并验证；
- 已配置但尚未真实验证；
- 需要用户完成的外部动作；
- 未完成及原因。
---END---
