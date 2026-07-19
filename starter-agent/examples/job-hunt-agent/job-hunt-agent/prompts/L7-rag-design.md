# L7 · RAG 知识库设计文档

用途：需求确认后，为求职 Agent 的 RAG 能力生成 `rag-design.md`。本提示词只做设计，不生成任务计划，不修改代码。

---BEGIN---
你是我的 Agent 工程设计协作伙伴。请使用中文工作。

前提：
- `rag-requirements.md` 已经确认。
- 现在只生成 `rag-design.md`，不要生成 `rag-task.md`，不要修改代码。

目标：
为 Starter Agent 设计「带引用、可更新、可删除的个人知识库 / RAG 能力」，服务求职 Agent 场景。

请先阅读现有仓库：
- `src/starter_agent/`
- `src/starter_agent/tools/`
- `config/config.example.yaml`
- `tests/`
- 已有求职工具、邮件工具、context/todo 工具相关实现和文档

然后生成 `rag-design.md`。

`rag-design.md` 必须包含以下章节：

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

设计必须说明：

1. 前端知识库入口：
   - Starter Agent 导航中新增「知识库」入口。
   - 页面提供文件选择、上传按钮、入库状态、文档列表、Chunk 数量、Chunk 预览和删除动作。
   - 上传失败、解析失败、索引失败必须在界面显示明确状态，不允许静默失败。
2. 上传 API 与 Ingestion Pipeline：
   - Upload
   - Parse
   - Normalize
   - Chunk
   - Metadata
   - Embed
   - Index
3. Query Pipeline：
   - Question
   - Retrieve
   - Metadata Filter
   - optional Rerank
   - Context
   - Answer
   - Citation
4. Metadata 至少包含：
   - `document_id`
   - `filename`
   - `page / section`
   - `user / project`
   - `version`
   - `created_at`
5. 引用与拒答策略：
   - 有证据时必须引用来源。
   - 无证据时明确拒答。
6. 文档生命周期：
   - 更新如何处理旧 chunk。
   - 删除如何同步删除 chunk 和向量。
7. Retrieval 与 Generation 如何分开评测。

约束：

- 不要把 RAG 设计成“上传文件后让模型自由发挥”。
- 不要忽略权限过滤和删除验证。
- 不要上传或索引敏感资料。
- 输出 `rag-design.md` 后停止，等待我确认设计。
---END---
