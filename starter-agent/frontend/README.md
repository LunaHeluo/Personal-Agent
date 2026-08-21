# Frontend

前端静态应用位于 `frontend/web/`：

- `index.html`：页面结构与可访问性语义
- `app.js`：现有 Chat、Knowledge、Capability、Trust 与工作台装配
- `app/`：API client、router、store 和功能模块
- `styles/`：基础令牌、旧功能样式与工作台样式

本地预览（从项目根目录执行）：

```powershell
python -m http.server 8001 --directory frontend/web
```

前端不保存权威业务状态；所有简历、版本、岗位、投递、复盘、导出和提醒均以后端 API 为准。

