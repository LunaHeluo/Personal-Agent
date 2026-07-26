# Job-research acceptance record

This file defines evidence and records the completed Task 16 acceptance run.

## Passing rules

Before an acceptance run has produced evidence, its status is `not_recorded`;
it must never be inferred from configuration or model narration.

A real success is PASS only when one trace links all of the following:

- loaded MCP config hash and actual Node/npx/runtime versions;
- real process start and MCP initialize response;
- real Tools/Resources/Prompts discovery snapshot and reviewed Tool schema hash;
- server-scoped refresh and Registry/model Context exposure state;
- Gate decision, required confirmation, re-check, and single-use execution;
- real public JD Tool Result with final URL and governed artifact reference;
- validated JD fields, scoped RAG evidence references, and user-confirmed
  ingestion status.

An MCP-unavailable degradation is PASS only for degradation behavior: it must
show the stable stage error, `dependency_unavailable`, no fabricated browser
result, no unsupported fit claim, and no unconfirmed JD write. It is not a
substitute for the real success record.

The following are **NOT PASS** evidence:

- a Mock client, fixture server, or mocked page;
- the fact that configuration exists;
- package documentation or a hard-coded Tool/schema list;
- component/unit tests alone;
- UI state without matching Manager/Registry/Store evidence;
- model narration or a model-written statement that a Tool succeeded.

## Evidence location

Use non-sensitive references, not copied payloads:

| Evidence | Required location |
| --- | --- |
| Server lifecycle and discovery | capability management response plus sanitized `logs/agent.jsonl` event reference |
| Active snapshot and reviewed schema | capability Store snapshot ID and `runtime_registry` catalog export |
| Gate and confirmation | capability audit event IDs and confirmation ID |
| Tool Result | tool trace call ID, `raw_source_ref`, final public URL, content hash, and size/truncation summary |
| RAG evidence | scoped chunk/source references and document version identifiers, without resume body in this file |
| JD ingestion | approval/ingestion record ID and knowledge document/version reference |

Do not record secrets, API keys, tokens, cookies, authorization headers, browser
profiles, personal login information, or resume text.

## Real success record

Status: `passed`

Recorded at: `2026-07-26T16:32:00+08:00`

- Command: `pytest tests/e2e/test_playwright_job_research.py -m external -q`
- Result: both real success and unavailable-degradation scenarios passed.
- Config: Starter Agent loaded `config/mcp.json`; the configured command remained
  `npx @playwright/mcp@latest`.
- Runtime: Node `v22.14.0`, npx `10.9.2`, Playwright
  `1.62.0-alpha-1783623505000`, MCP protocol `2025-11-25`.
- Discovery: snapshot `playwright-snapshot-2`, version `2`, schema hash
  `b878306bb4ad8fc25a5ae9d0870a32e85f863e349d1bddbe996413b9d64c8e07`,
  `24` Tools, `0` Resources, `0` Prompts.
- Public source:
  `https://jobs.lever.co/payugpo/49975338-7270-422e-a3c1-e2375394cef4`.
- Runtime flow: a scripted Provider requested real `browser_navigate` and
  `browser_snapshot` calls. Both calls emitted a persisted confirmation before
  any `tool.invoked` event, then executed once after an approved one-shot
  decision.
- Result processing: the real Playwright accessibility snapshot passed through
  `McpToolResultAdapter`, `ToolResultGuard`, restricted Artifact persistence,
  and the `tool.completed` audit path. The Artifact contained non-empty title,
  company, location, responsibilities, and requirements extracted from the real
  page.
- Evidence references: the test asserts the persisted restricted Artifact,
  content SHA-256, final source URL, schema hash, confirmation IDs, audit ID,
  and `trace:<session>:<turn>:<call>` reference. Values are generated per run
  and remain in the isolated acceptance database rather than this repository.
- Context exposure: real Provider requests observed context revisions `4`, `5`,
  and `6`; disabling `browser_snapshot` removed its full definition from the
  next Provider request, and re-enabling plus approval restored it.
- RAG: a temporary, non-sensitive resume fixture was uploaded through the real
  knowledge ingestion pipeline and returned source reference
  `task16-public-resume-fixture.md@v1#L5-L5`.
- JD ingestion: while approval was pending there was no `job_description`
  document. After persisted approval, the real knowledge pipeline created a JD
  document and retrieval returned a versioned `source_ref`.
- UI: the same real MCP Manager/Registry/Store was exposed through the actual
  capability API and production HTML. Playwright opened the loopback page via
  the network Guard, read the `MCP Servers` tab, and verified the real
  `playwright` Server and `browser_snapshot` Tool in a persisted Artifact with
  a matching Trace reference. The loopback exception is exact-origin and only
  enabled when explicitly supplied by the host/test.

## MCP unavailable degradation record

Status: `passed`

Recorded at: `2026-07-26T16:32:00+08:00`

- Scenario: an MCP Server configured with a deliberately nonexistent local
  command was started through the real `McpManager`; no mock MCP Server or
  fabricated Tool result was used.
- Authoritative state: connection `failed`, health `unhealthy`, operation
  `degraded`, with a stable non-empty transport/initialization error code.
- Capability state: no active snapshot and no snapshot summary were created.
- Safety result: no Browser Tool was callable, no JD content was generated, and
  no knowledge document was written.
- The non-sensitive platform error was retained as the Server's `last_error`;
  its localized text is intentionally not treated as a stable assertion.

## Final determination

The real Runtime/MCP/Artifact/RAG/ingestion/UI path and unavailable degradation
record pass. Overall Task 16 is `PASS`, subject to the fresh complete regression
recorded with the implementation handoff.
