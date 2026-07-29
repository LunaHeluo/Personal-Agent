# Job Search Confirmation and Location Recovery Design

## Goal

Restore location-based job research after the SerpAPI tool schema expansion,
without weakening confirmation policy for external MCP tools or user-created
rules. The repaired flow must also recover a provider-supported Latin location
alias when SerpAPI's Location API does not recognize the user's original local
script.

## Diagnosed Failures

The production trace identified four separate boundaries:

1. The Bootstrap-created `allowlist_auto` rule for
   `search_jobs_serpapi` is bound to the previous tool schema hash. The trusted
   builtin now has a different schema hash, so the rule no longer matches and
   the Gate correctly returns `require_confirmation`.
2. `JobResearchOrchestrator._call` converts `require_confirmation` directly
   into `tool_confirmation_required`. It does not use the runtime confirmation
   coordinator, persist a pending confirmation, or wait for a decision.
3. The buffered `job_research` streaming route emits only the final text and
   does not relay confirmation events to the chat UI.
4. SerpAPI's Location API returns no rows for Chinese `上海`, while `Shanghai`
   resolves successfully. The planner therefore degrades to the original
   location and loses the Latin alias.

The model search-profile stage is independently intermittent: the audited
response can contain all expected field names but still fail a field-level
Pydantic constraint. Current audit events do not record safe validation issue
categories, so the exact invalid field cannot be distinguished.

## Policy Rule Migration

At application bootstrap, reconcile only automatically generated builtin
allowlist rules satisfying every condition below:

- rule ID is exactly `builtin-auto-<canonical builtin name>`;
- `server_id` is `builtin`;
- `created_by` is `bootstrap`;
- effect is `allowlist_auto`;
- tool is still enabled, connected, reviewed, and risk level `read`;
- actions remain exactly the existing bounded read action.

If such a rule's schema hash differs from the current builtin schema, replace
the rule binding with the current schema hash while preserving its bounded
scope. Never migrate user-created rules, external MCP rules, write/external
risk tools, disabled tools, rejected tools, or rules with unexpected effects
or actions.

This restores the previously approved read-only search behavior. It does not
treat arbitrary schema changes as user approval because only the application's
own deterministic Bootstrap rule is eligible.

## Confirmation Event Path

`JobResearchOrchestrator` will receive an optional confirmation coordinator
and optional tool-event callback through its execution context. When the Gate
returns `require_confirmation`, the orchestrator will use the same
`TurnCoordinator.wait_for_permit` path as `AgentRuntime`:

1. persist a pending confirmation bound to session, turn, call, schema and
   arguments;
2. emit `confirmation_required` to the current SSE stream;
3. wait for the current session's decision;
4. revalidate the Gate and execute exactly once after approval;
5. return precise cancellation, expiry or invalidation errors without invoking
   the tool.

The buffered job-research stream will expose a queue-backed `on_tool_event`
callback and relay events while the classified job task is running. It must
preserve its existing final `delta` and `done` events. A disconnected browser
must not cancel the persisted governed turn.

Even though the reconciled SerpAPI read rule normally auto-allows, this path is
required for non-Bootstrap rules, future reviewed schema transitions, and
Playwright capabilities whose policy requires confirmation.

## Generic Location Alias Recovery

Location recovery must remain independent of any city table.

1. Query SerpAPI Location API with the normalized original location.
2. If it returns no rows and the input contains non-Latin characters, obtain a
   minimal Latin location candidate from the already selected structured model
   boundary. The response contract contains only `location_alias` and must not
   include resume evidence or other personal data.
3. Re-query the Location API with that candidate.
4. Accept only a provider-returned canonical location, city alias and ISO
   country code. The model candidate is never trusted as canonical by itself.
5. If either translation or provider validation fails, retain the existing
   original-location degraded plan and reason code.

The implementation must support arbitrary regions and scripts, including
`上海`/`Shanghai` and `München`/`Munich`, without local hardcoded mappings.

## Search and Timeout Behavior

Both `google_jobs` and ordinary `google` organic requests remain enabled.
Per-query failures continue to be recorded with engine, query and error code.
Successful organic results are returned even if every Google Jobs request
times out. Partial engine failures must not change an otherwise successful Tool
result into a total failure.

## Search-Profile Diagnostics

For each failed profile attempt, persist only safe Pydantic issue summaries:
field path and validation error type. Do not persist generated model content,
resume text, invalid values, prompts, or secrets. Existing output length,
field-name and request-ID audit data remains.

The user-facing error stays concise but may state which contract fields failed,
for example `evidence_refs:list_type`, when available.

## Tests

Implementation follows strict red-green TDD. Required tests:

- a stale Bootstrap-owned read-only builtin rule is rebound to the current
  schema and the Gate auto-allows it;
- user-created, external, non-read, disabled and unexpected-scope rules are not
  migrated;
- a job Skill requiring confirmation persists a pending record, emits an SSE
  confirmation event, waits, revalidates and invokes exactly once after
  approval;
- cancellation and timeout never invoke the Tool and return specific codes;
- the buffered job-research stream relays confirmation events before the final
  result;
- a non-Latin location miss is retried through a provider-validated Latin alias;
- an invalid or unavailable alias degrades to the original location;
- partial Google Jobs timeouts still return successful organic candidates;
- profile audit events record safe field/type issue summaries and no raw model
  or resume content.

Focused job-research, confirmation, policy, API and location suites must pass.
The complete test suite will also be run; unrelated pre-existing failures will
be reported separately and not silently modified.

## Non-Goals

- Automatically migrating user-created policies.
- Hardcoding Chinese city translations.
- Disabling Tool governance or auto-approving write/external actions.
- Treating model-generated location text as canonical without provider
  validation.
- Making Google Jobs availability a prerequisite for organic search success.
