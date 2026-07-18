# L4 · Resume Tools · 任务书

---BEGIN---
请为求职 Agent 设计并生成简历工具草案。

读取：
- `starter-agent/docs/agent.md`
- `starter-agent/docs/workflow.md`
- `examples/job-hunt-agent/data/resume_base.md`
- `examples/job-hunt-agent/data/jobs/job_001_srd_agent.json`

工具范围：

1. `read_resume`
   - risk_level: `read`
   - 输入：`path`
   - 输出：简历段落、技能、项目经历摘要、来源文件路径。
   - 失败：文件不存在、格式不支持、内容过长。

2. `draft_resume_patch`
   - risk_level: `write`
   - 输入：`resume_path`、`job_id` 或 `job_description`、`target_section`
   - 输出：diff / patch，不直接覆盖原文件。
   - 必须标注每条修改建议依据哪段真实经历。

3. 可选 `save_resume_version`
   - risk_level: `write`
   - 只能保存新版本，例如 `resume_agent_v2.md`，不能覆盖原简历。

强边界：
- 不得编造 RAG、MCP、LangGraph 等经历。
- 不得把“假设我做过”当成真实简历事实。
- 如果 JD 要求与真实经历不匹配，应标出 gap，而不是补假经历。

验收案例：
- 正常：读取 resume_base.md + job_001，生成有依据的修改建议。
- 失败：resume_path 不存在，进入 `waiting_for_user`。
- 安全：用户要求夸大经历，拒绝并给真实替代表达。
---END---
