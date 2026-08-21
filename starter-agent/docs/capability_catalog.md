# Capability catalog

This document is a review aid, not an authorization source. Runtime authority
comes from the `runtime_registry` export at
`GET /v1/capabilities/catalog/export`, the active capability snapshot, the
policy store, and the Pre-Tool-Call Gate. Editing this Markdown file cannot
enable, review, allowlist, or expose a capability to model Context.

The table records the required review fields. “Dynamic” means the value must be
read from the runtime export or management API for the inspected process; it
must not be copied from this document into an authorization decision.

| Category | Name | Source | Config version | Runtime version | Transport | Capability | Enabled state | Context exposure | Risk | Allowlist | Always confirm | Outbound data | Owner | Health check | Disable |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Built-in Tool | `ToolRegistry` entries | `backend/src/starter_agent/tools/registry.py` and runtime settings | Git revision plus settings file; dynamic | Application Python environment; dynamic | in-process | Time, public job search/read, resume operations, and email operations according to the enabled Registry entries | `catalog_export.builtins[].enabled` | Only enabled and policy-allowed tools enter the callable model snapshot | Per-tool `risk_level`; read/write/external | Policy store and Gate, never this table | Gate policy; write/external and explicit `always_confirm` rules cannot be inferred from this table | Per-tool; inspect Gate request data classes and destination | Starter Agent maintainer | Resolve the tool in Registry and inspect the next model snapshot | Persist a Registry override through the management API; do not edit Markdown |
| RAG Tool | `retrieve_resume_evidence` | `backend/src/starter_agent/tools/builtin/knowledge.py` | Git revision plus knowledge settings; dynamic | Application Python environment; dynamic | in-process / local SQLite | Scoped retrieval of resume evidence with source references | `catalog_export.builtins[]` for this exact name | Lightweight name may be listed; the complete schema appears only when callable | read, sensitive local data | No external destination allowlist; scope and Gate still apply | Not intrinsically always-confirm; effective policy is authoritative | No external data by design; result is bounded before model Context | Starter Agent maintainer | Resolve the tool, verify knowledge dependency/scope, and run the RAG acceptance tests | Disable through the Registry management API |
| Skill | `job-research` | `backend/src/starter_agent/skills/job-research/SKILL.md`, loaded by `SkillRegistry` | Skill `version` and `snapshot_hash`; dynamic | Skill load state and dependency state; dynamic | in-process orchestration | Governed search, selected public-page read, JD validation, RAG evidence, analysis, and confirmation before ingestion | Skill Registry state; dynamic | Lightweight metadata first; full definition only after selection | Inherits dependency risks; no direct Tool bypass | Every dependency request is re-evaluated by the Gate | Inherits dependency policy; ingestion needs explicit confirmation | Structured public search/page inputs and bounded results; resume evidence stays within governed RAG flow | Starter Agent maintainer | Skill health endpoint plus dependency state | Disable through the Skill management API |

## Playwright MCP

| Category | Name | Source | Config version | Runtime version | Transport | Capability | Enabled state | Context exposure | Risk | Allowlist | Always confirm | Outbound data | Owner | Health check | Disable |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| MCP Server | `playwright` | `config/mcp.json` launch declaration | Config hash is dynamic; the package selector remains `@latest` | Last acceptance observation: `Playwright 1.62.0-alpha-1783623505000`; runtime remains dynamic | stdio | Last acceptance discovery: 24 Tools, 0 Resources, 0 Prompts; snapshot hash `892b496f1d4dd14d4c106287a630d64d4ed8a9a09b0f70b0857199db0ac5e15c` | Manager/Registry state; dynamic | No MCP Tool schema enters Context without an active snapshot, enablement, approval, connection, and policy exposure | Per-Tool runtime review; newly discovered Tools default to `unreviewed` and disabled | Per-Tool policy store and Gate; no blanket Server allowlist | Gate action policy; forbidden and always-confirm actions cannot be bypassed | Public HTTP(S) URL and bounded page result for approved read flows; credentials and login data prohibited | Starter Agent maintainer | Server health API, process state, initialize state, discovery snapshot, then a governed Tool call | Use the confirmed disconnect/disable management operation |

Discovery state: `observed_in_acceptance_2026-07-26`; authoritative current state
must still be read from the running Manager and active snapshot.

Review state: dynamic per Tool. Fresh discoveries are `unreviewed` and disabled.
The isolated acceptance run explicitly approved and enabled only the Tools needed
for that run; it did not grant persistent production authorization.

The 2026-07-26 acceptance process loaded `config/mcp.json`, started
`npx @playwright/mcp@latest`, initialized protocol `2025-11-25`, and observed
Node `v22.14.0`, npx `10.9.2`, an empty redacted stderr tail, healthy/ready
runtime state, and a clean Manager transition to `closed`. The SDK transport did
not expose an operating-system exit code, so both the running and post-shutdown
`exit_code` fields were `null`; this must not be rewritten as numeric zero.

The minimum public-JD Tool evidence below is copied from that real discovery,
not from package documentation. Full descriptions and schemas remain available
only through the administrator Schema endpoint and active runtime snapshot:

| Upstream Tool | Model alias | Real description | Input contract summary | Schema hash | Default discovery state | Risk/data scope |
| --- | --- | --- | --- | --- | --- | --- |
| `browser_navigate` | `mcp__playwright__browser_navigate` | `Navigate to a URL` | Object; required string `url`; no additional properties | `2165538e098634780eec628947d795a2619b4d2e3cef0e36d3084ac46abb94f7` | disabled, `unreviewed` | External navigation to Gate-approved public HTTP(S) URL; login and submission prohibited |
| `browser_snapshot` | `mcp__playwright__browser_snapshot` | `Capture accessibility snapshot of the current page, this is better than screenshot` | Object; optional `target` string, `filename` string, `depth` number, `boxes` boolean; no additional properties | `36ee5bbb5798a52e26015635e1f6015b8f4b62f44119d53ad2516837667fcd61` | disabled, `unreviewed` | Read current approved page; bounded, redacted result with final source URL and Trace |

## Export safety and review procedure

The Registry export is an allowlist projection. It includes governance
identifiers, enabled/review state, hashes, and non-sensitive lifecycle fields;
it omits input schemas, descriptions, metadata, stderr, arguments, Tool
results, environment values, credentials, cookies, tokens, login data, and
resume content.

For a catalog review:

1. Read `GET /v1/capabilities/catalog/export` as a viewer.
2. Match reviewed MCP Tool names and schema hashes to the active snapshot.
3. Confirm the policy store separately; the export and this document do not
   grant permission.
4. Update this document only with evidence references that contain no secret,
   personal login data, or resume body.
