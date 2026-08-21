# 项目整理与临时文件清理记录

日期：2026-08-17

## 目录迁移

- 前端：`src/web/` → `frontend/web/`
- 后端：`src/starter_agent/` → `backend/src/starter_agent/`
- Python 构建入口已改为 `backend/src/starter_agent`。
- `PROJECT_ROOT` 层级、Skill Registry、测试和文档路径引用已同步更新。
- 根级 `tests/` 作为跨前后端契约与集成测试保留。

## 已删除内容

- 245 个以上 pytest/session/task 临时目录、源码 `__pycache__` 和本地构建缓存。
- 所有符合 `api|context|mvp|research-<32位十六进制>.db` 的隔离测试数据库。
- Task14 遗留的三个 Chrome/Edge 测试 profile；仅终止了命令行精确引用这些 profile 的 28 个无头测试进程。
- Task21 临时办公软件 profile、Task24 浏览器失败 profile、Task25 浏览器 profile（保留最终 PNG）。
- 空的旧根级 `skills/` 目录；权威 Skill 位于后端包内。

## 明确保留内容

- 正式数据库：`api.db`、`context.db`、`workbench.db`。
- `data/`、`logs/`、配置和迁移 registry。
- `artifacts/task21-qa` 与 `artifacts/task25-qa` 中的验收成品。
- Delegation、Orchestration、Trust 和 Job Search 的验收/诊断 Artifact。

## 防复发

`.gitignore` 已覆盖随机测试数据库、pytest/session/task 目录、浏览器测试 profile、`__pycache__` 和项目级 uv cache。

## 验证

- `uv sync --extra dev --offline` 成功，editable package 指向 `backend/src/starter_agent`。
- 关键迁移回归：185 passed。
- 最终 unit/integration：1665 passed，1 个上游弃用 warning，0 failed。
- 前端全部 JavaScript 通过 `node --check`。

