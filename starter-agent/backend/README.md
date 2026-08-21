# Backend

Python 后端位于 `backend/src/starter_agent/`，继续以 `starter_agent` 作为导入包名。

主要目录：

- `agent/`：Agent Runtime 与上下文治理
- `cv_workbench/`：简历、版本、岗位、分析、投递、复盘、导出和统计
- `interfaces/`：CLI、FastAPI、Run/Task 与 Workbench API
- `knowledge/`：本地知识库与证据
- `capabilities/`、`mcp/`、`trust/`：能力治理、安全和发布门禁
- `delegation/`、`orchestration/`：后台任务与编排

构建入口仍在项目根目录的 `pyproject.toml`。完成目录迁移后运行 `uv sync` 可刷新 editable install。

