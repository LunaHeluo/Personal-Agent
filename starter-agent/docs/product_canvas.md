# Product Canvas

## User

正在求职的 IT master 毕业生 Luna。目标岗位是 Junior AI Agent / AI Product
Manager，地点偏好为上海、深圳、杭州、成都。

## Trigger

用户准备投递 AI Agent 或 AI Product 相关岗位，手里有基础简历和岗位 JD，需要快速判断
“这份 JD 与我的产品、前端、RAG 和 AI 实习经历如何匹配，简历应该怎么改”。

## Current Workflow

用户先阅读 JD，再回到基础简历中寻找相关经历。她需要判断会议推荐系统是否能对齐
Agent workflow 和工具链设计，RAG 问答机器人是否能对齐大模型应用开发，TAL AI 产品实习
是否能对齐需求研究、MVP 实验和数据分析。之后再手动修改简历表达，并决定是否投递。

## Pain

- JD 偏研发，用户不确定如何呈现产品能力、前端能力和 AI 应用能力的组合优势。
- 简历中经历较多，用户不知道哪几段最该服务于当前 JD。
- 用户担心把项目或实习写得过度，尤其是工程深度、模型能力和业务影响。
- 投递前缺少可验收的匹配分析、修改优先级和风险提醒。

## Inputs

- 基础简历 `examples/job-hunt-agent/job-hunt-agent/data/resume_base.md`。
- 岗位 JD `examples/job-hunt-agent/job-hunt-agent/data/job_001_srd_agent`。
- 个人背景：学历、项目经历、实习经历。
- 求职偏好：目标岗位、城市偏好。

## Output

- 针对该 JD 的匹配分析。
- 简历修改建议。
- 风险提醒：哪些内容不能夸大或捏造。
- 投递前 checklist。

## Human Decisions

- 是否投递岗位。
- 是否采用最终简历修改。
- 是否调整岗位方向或投递优先级。
- 是否新增、删减或改写经历。

## Metrics

- 能否在一次分析中明确 3 个以上匹配点和主要风险。
- 能否给出按优先级排序的简历修改建议。
- 用户是否能基于 checklist 做出投递、暂缓投递或调整方向的决定。
