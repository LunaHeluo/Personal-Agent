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
| Built-in Tool | `ToolRegistry` entries | `src/starter_agent/tools/registry.py` and runtime settings | Git revision plus settings file; dynamic | Application Python environment; dynamic | in-process | Time, public job search/read, resume operations, and email operations according to the enabled Registry entries | `catalog_export.builtins[].enabled` | Only enabled and policy-allowed tools enter the callable model snapshot | Per-tool `risk_level`; read/write/external | Policy store and Gate, never this table | Gate policy; write/external and explicit `always_confirm` rules cannot be inferred from this table | Per-tool; inspect Gate request data classes and destination | Starter Agent maintainer | Resolve the tool in Registry and inspect the next model snapshot | Persist a Registry override through the management API; do not edit Markdown |
| RAG Tool | `retrieve_resume_evidence` | `src/starter_agent/tools/builtin/knowledge.py` | Git revision plus knowledge settings; dynamic | Application Python environment; dynamic | in-process / local SQLite | Scoped retrieval of resume evidence with source references | `catalog_export.builtins[]` for this exact name | Lightweight name may be listed; the complete schema appears only when callable | read, sensitive local data | No external destination allowlist; scope and Gate still apply | Not intrinsically always-confirm; effective policy is authoritative | No external data by design; result is bounded before model Context | Starter Agent maintainer | Resolve the tool, verify knowledge dependency/scope, and run the RAG acceptance tests | Disable through the Registry management API |
| Skill | `job-research` | `skills/job-research/SKILL.md`, loaded by `SkillRegistry` | Skill `version` and `snapshot_hash`; dynamic | Skill load state and dependency state; dynamic | in-process orchestration | Governed search, selected public-page read, JD validation, RAG evidence, analysis, and confirmation before ingestion | Skill Registry state; dynamic | Lightweight metadata first; full definition only after selection | Inherits dependency risks; no direct Tool bypass | Every dependency request is re-evaluated by the Gate | Inherits dependency policy; ingestion needs explicit confirmation | Structured public search/page inputs and bounded results; resume evidence stays within governed RAG flow | Starter Agent maintainer | Skill health endpoint plus dependency state | Disable through the Skill management API |

## Playwright MCP

| Category | Name | Source | Config version | Runtime version | Transport | Capability | Enabled state | Context exposure | Risk | Allowlist | Always confirm | Outbound data | Owner | Health check | Disable |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| MCP Server | `playwright` | `config/mcp.json` launch declaration | Config hash is dynamic; the package selector is not a resolved version | `not_recorded` until a real initialize response supplies runtime identity | stdio | `not_discovered`; no Tool capability is asserted in Task 14 | Manager/Registry state; dynamic | No MCP Tool schema may enter Context without an active snapshot, enablement, approval, connection, and policy exposure | `not_reviewed` | No Tool allowlist conclusion exists before discovery and review | No Tool always-confirm conclusion exists before discovery and review | None recorded because no real Tool has been accepted | Starter Agent maintainer | Server health API, process state, initialize state, then snapshot summary | Use the confirmed disconnect/disable management operation |

Discovery state: `not_discovered`

Review state: `not_reviewed`

Snapshot evidence checked for Task 14: the local capability store had no active
Playwright capability snapshot and no discovered Playwright Tool rows. This is
not a Task 16 discovery run. Therefore there are no upstream names, model
aliases, schema hashes, review timestamps, or review conclusions to list here.
Do not add a Tool row until a real protocol discovery snapshot exists. When it
does, copy only the reviewed `upstream_name`, `model_alias`, `schema_hash`,
review timestamp evidence, and conclusion from the authoritative runtime
records; never infer them from package documentation, the Skill dependency
text, a mock, or prior knowledge.

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
