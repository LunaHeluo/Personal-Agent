# Job-research operations

Use this runbook stage by stage. Retry only the failed stage once after fixing
its stated prerequisite. Do not repeatedly restart or refresh: repeated
attempts can hide the first useful exit code or stderr summary. Values shown by
the Manager, Store, API, audit trace, and Tool Result are authoritative; a
Markdown statement is not.

| Stage | Observable state | Stable error code | Minimal retry |
| --- | --- | --- | --- |
| Node | Executable resolution and the `node_version` returned with initialized client metadata | `node_not_found` | Install or expose one trusted Node executable, then retry connect once |
| npx | Executable resolution and `npx_version`; no inline credential is permitted in command/args/env | `npx_not_found` | Fix the trusted executable path, then retry connect once |
| package cache | npx cache hit/download diagnostics in sanitized stderr and process exit code | `connect_failed` or the recorded client error | Repair cache permissions or network once; retry connect, not the full workflow |
| process | Manager `connection_state`, `operation_state`, PID, sanitized stderr summary, exit code, and transport closure | `connect_closed`, `session_closed`, or `connect_failed` | Read the first exit/stderr evidence, correct it, then create one candidate process |
| initialize | `initializing` followed by protocol/runtime metadata and `ready` | `initialize_timeout` or the client protocol error | Verify process responsiveness and timeout setting; reconnect once |
| discovery | `discovering`, candidate counts, snapshot version/hash, active/stale flag, and discovery timestamp | `discovery_failed`, `invalid_tool_schema`, `model_alias_collision`, `duplicate_tool_name`, or `invalid_discovery_cursor` | Correct the reported schema/name/page issue and run one server-scoped refresh |
| browser dependencies | Real Tool error/result and sanitized process diagnostics from the discovered browser capability | `mcp_tool_error` or the recorded runtime error | Install only the missing browser dependency indicated by real evidence, then retry that Tool once |
| Gate | Gate decision, `reason_code`, server/tool/snapshot/schema binding, destination, and inferred data classes in audit | `tool_not_found`, `server_not_connected`, `snapshot_missing`, `stale_snapshot`, `tool_disabled`, `tool_rejected`, `schema_hash_mismatch`, or the recorded policy reason | Restore the authoritative Registry/policy precondition and resubmit one request |
| confirmation | Pending record, expiry, decision, request hash, policy revision, and audit events | `confirmation_timeout`, `confirmation_policy_changed`, or `allowlist_forbidden_always_confirm` | Obtain a fresh confirmation after re-running Gate; never reuse an invalidated decision |
| Tool Result | `ok`, `error_code`, source/provenance reference, final public URL, byte/character/token counts, and truncation fields | `mcp_tool_error`, `tool_invocation_failed`, or the Tool-specific stable code | Retry only an idempotent read after checking source and size; never synthesize missing content |
| refresh | Target server `operation_state`, revision, candidate snapshot, atomic swap, old snapshot stale state, and drain result | `refresh_in_progress`, `revision_conflict`, `refresh_failed`, `drain_timeout`, or `close_timeout` | Re-read target server revision; retry only that server once after the in-flight refresh finishes |

## Stage procedure

### Node, npx, cache, and process

Start with the management server detail and sanitized logs. Record executable
versions only when the client actually returned them. A configured command or
an installed executable does not prove that the child process initialized.
Never paste environment values, authorization strings, or registry credentials
into an incident record.

### initialize and discovery

An initialize success must precede discovery. Discovery is acceptable only
when Tools/Resources/Prompts came from the running MCP session, passed bounded
metadata and JSON-schema validation, and were persisted as a candidate
snapshot. Activation is atomic. If validation fails, the prior active snapshot
remains authoritative; do not manually copy a schema into the Store or catalog.

### browser dependencies

Task 14 does not probe a browser or install browser binaries. Diagnose browser
dependencies only from a real discovered Tool invocation. Preserve a
non-sensitive error code and artifact/trace reference, not page content,
cookies, profiles, or login state.

### Gate and confirmation

The request must bind server, canonical Tool name, active snapshot, schema
hash, arguments hash, destination, and data classes. The Gate is re-run after a
decision. An `always_confirm` decision cannot be converted into a persistent
allowlist. A cancelled, expired, invalidated, or consumed confirmation cannot
authorize a call.

### Tool Result and refresh

Keep raw result material behind its governed artifact reference. Logs and
catalog exports may contain only bounded summaries and hashes. A refresh uses a
candidate client/snapshot and swaps only after validation; failure must preserve
the prior active snapshot and must not refresh another server.
