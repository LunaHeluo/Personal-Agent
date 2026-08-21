# 项目目录归属

```text
starter-agent/
├─ frontend/
│  └─ web/                    浏览器端静态应用
├─ backend/
│  └─ src/starter_agent/      Python 后端与 Agent Runtime
├─ tests/                     跨层单元、集成、E2E 和契约测试
├─ config/                    前后端共享运行配置
├─ docs/                      需求、设计、ADR 和验收记录
├─ data/                      正式本地数据（不作为源码）
├─ artifacts/                 保留的验收/导出证据
├─ evals/                     Trust 与 Agent 评测集
├─ scripts/                   工程脚本
└─ pyproject.toml             后端构建、依赖和 pytest 配置
```

## 归属原则

- 浏览器可执行的 HTML/CSS/JavaScript 只进入 `frontend/web/`。
- 可导入的 Python 包、API、数据库 Store 和 Agent 能力只进入 `backend/src/starter_agent/`。
- 测试同时验证 API 与 UI 契约，保留在根级 `tests/`，不复制两套 Fixture。
- 配置、文档、数据、Artifact 与 Eval 属于项目共享资源，不归入单一运行端。
- 根目录只保留稳定数据库 `api.db`、`context.db`、`workbench.db`；带随机后缀的测试数据库由 `.gitignore` 排除。

## 常用命令

```powershell
uv sync
uv run agent doctor
uv run pytest
python -m http.server 8001 --directory frontend/web
```

