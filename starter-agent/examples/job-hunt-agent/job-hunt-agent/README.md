# Job Hunt Agent Classroom Pack

本目录用于第 1-3 课主线版：编程 Agent -> 求职 Agent -> 你的 Personal Agent。

## 给学生

课堂只使用这些内容：

- `data/`: Eason mock 简历与岗位 JD。
- `prompts/`: 复制到编程 Agent 的任务书，不是答案。
- `docs/*.template`: 空模板或极短骨架。
- `evals/*.template`: 验收案例字段说明。

其中 `data/jobs/job_001_srd_agent.json` 基于字节跳动招聘官网与猎聘公开岗位信息整理，课堂使用时仍视为教学 mock 数据；正式投递前必须回到招聘官网核对实时信息。

不要直接抄成品。每个产物都要由你在编程 Agent 辅助下生成，并用边界测试或 acceptance cases 检查。

## 给教师

`teacher-kit/` 只放应急说明或教师私有参考稿索引。正式发给学生的 zip / 链接应排除 `teacher-kit/`。
