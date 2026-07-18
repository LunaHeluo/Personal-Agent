# Architecture

```text
CLI / FastAPI
      |
ApplicationService
      |
AgentRuntime ---- ToolRegistry / ToolPolicy
      |
ProviderRegistry
      |
Mock or OpenAI-compatible provider

ApplicationService ---- SQLiteSessionStore
```

The interfaces share one application service. Provider-specific request formats
stay behind provider adapters, and tool execution is bounded by policy and hard
runtime budgets.`r`n`r`n## System / Context Layering

System prompt content is reserved for stable behavior: the agent identity, safety boundaries, approval rules, workflow references, and tool policy. It should remain reusable across job applications and should not include a single JD, one user's private resume details, or temporary application state.

Runtime context is where changing task data belongs. This includes the current JD, base resume, target role, user preferences, session history, acceptance state, and whether the agent is waiting for user input, waiting for approval, completed, or failed.

The agent may use runtime context to produce a matching analysis or resume suggestions, but must not promote that data into long-term stable prompts. When context is missing, conflicting, or too weak to support a reliable answer, the workflow should stop in `waiting_for_user`, `waiting_for_approval`, or `failed` instead of inventing facts.

