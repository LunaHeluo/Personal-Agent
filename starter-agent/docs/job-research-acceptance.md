# Job-research acceptance record

This file defines evidence and records Task 14 status. It does not claim a real
Playwright end-to-end run. Task 16 is responsible for producing those records.

## Passing rules

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

Status: `not_recorded`

Task 14 did not run Playwright discovery, review, or a real browser Tool. No
version, Tool name, schema hash, success trace, URL, or artifact is asserted.

## MCP unavailable degradation record

Status: `not_recorded`

No Task 16 degradation exercise was run. A configured server state or a failed
background attempt without a complete governed job-research trace is not an
acceptance record.

## Final determination

Task 14 documentation/export checks may pass independently, but real
job-research E2E acceptance remains `NOT PASS` until both records above contain
the required real evidence and the real success record passes every rule.
