# L5 · Token UI · 任务书

---BEGIN---
请为 Starter Agent 前端设计并生成 token 消耗显示方案。

目标：
- chat 每次回复后实时显示本轮 token 使用。
- 显示 prompt / completion / total。
- 显示本 session 累计 token。
- 当接近预算时显示 warning。
- 对于不知道max_token的模型，可以先默认为128k。

要求：
1. 后端 ChatResult 中保留 provider usage。
2. 前端 meta 行显示：
   - provider / model
   - tools=n
   - tokens=prompt/completion/total
3. 顶部或侧边显示 session token total。
4. 预算阈值可配置，例如 `context.max_total_tokens`。
5. mock provider 没有 usage 时显示 `tokens=mock`，不能伪造。

验收：
- 真实 provider 返回 usage 时 UI 可见。
- mock provider 不显示假 token。
- token 超过预算阈值时 UI 出现 warning。
---END---
