# SMTP Manual Approval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow a user to compose an email in Starter Agent, preview an immutable local draft, explicitly approve it in the web UI, and send it once through the configured SMTP profile.

**Architecture:** Real IMAP/SMTP profiles normalize model-selected mock draft scope to a local draft, while mock profiles remain simulated. Runtime tool events expose only the safe draft metadata needed by the browser; the browser obtains the complete preview from the trusted approval API. A dedicated approval-send endpoint executes the registered `email_send` tool under `ToolPolicy`, preserving the server-side approval and idempotency gates.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, pytest, vanilla HTML/CSS/JavaScript.

## Global Constraints

- Never send email in automated tests; use fake adapters or monkeypatches.
- Never expose account credentials, authorization codes, API keys, or SMTP protocol logs.
- A draft creation result must always state `sent=false`.
- A real send requires a draft-bound server-side approval created and confirmed by the user.
- Report success only when the send receipt has `status="sent"` and `external_delivery=true`.
- Never automatically retry `status="unknown"`.

---

### Task 1: Normalize Draft Scope for Real Profiles

**Files:**
- Modify: `src/starter_agent/tools/email/tools.py`
- Test: `tests/unit/test_email_tools.py`

**Interfaces:**
- Consumes: `EmailManager.resolve_profile(profile: str | None)`.
- Produces: `EmailCreateDraftTool.execute(arguments, context)` that defaults mock profiles to `mock` and IMAP/SMTP profiles to `local`.

- [ ] **Step 1: Write failing tool tests**

Add tests that omit `storage_scope` for a mock profile and pass `storage_scope="mock"` for a real profile. Assert the resulting scopes are `mock` and `local` respectively, and assert neither path calls SMTP.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
& '.venv\Scripts\python.exe' -m pytest -p no:cacheprovider tests/unit/test_email_tools.py -q
```

Expected: the new tests fail because `storage_scope` is required and a real profile rejects mock scope.

- [ ] **Step 3: Implement minimal scope normalization**

Make `storage_scope` optional in the tool schema. Resolve the selected profile before Pydantic validation and set:

```python
if configured.adapter == "mock_fixture":
    values["storage_scope"] = "mock"
elif values.get("storage_scope") in {None, "mock"}:
    values["storage_scope"] = "local"
```

Update the description to tell the model to omit scope unless it explicitly needs a provider mailbox draft.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run the Task 1 command and expect all tests to pass.

### Task 2: Add a Policy-Governed Approval Send API

**Files:**
- Modify: `src/starter_agent/interfaces/api.py`
- Modify: `src/starter_agent/agent/runtime.py`
- Test: `tests/integration/test_api.py`
- Test: `tests/integration/test_application.py`

**Interfaces:**
- Consumes: `EmailApprovalService.get`, registered `EmailSendTool`, `ToolPolicy`, and `ToolContext`.
- Produces: `POST /v1/email/approvals/{approval_id}/send` with `session_id` and `idempotency_key`.
- Produces: safe `metadata` on `tool_completed` stream events.

- [ ] **Step 1: Write failing API and runtime event tests**

Cover an unconfirmed approval rejection, an approved fake send returning one receipt, duplicate idempotency returning the same receipt, and a runtime draft event containing `draft_id` without body or credentials.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
& '.venv\Scripts\python.exe' -m pytest -p no:cacheprovider tests/integration/test_api.py tests/integration/test_application.py -q
```

Expected: the send endpoint is missing and runtime events do not contain metadata.

- [ ] **Step 3: Implement the endpoint and safe event metadata**

The endpoint must:

1. Retrieve the registered `email_send` tool.
2. Enforce `ToolPolicy(settings.tools.allow_risk_levels)`.
3. Read the approval and its bound draft from the current session.
4. Execute `EmailSendTool` with the stored content hash and caller idempotency key.
5. Return the `ToolResult` without exposing the draft body or credentials.

Runtime events may include `result.metadata`, which for email drafts contains only profile, draft ID, content hash, and `sent=false`.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run the Task 2 command and expect all tests to pass.

### Task 3: Add the Web Preview and Confirm-Send Card

**Files:**
- Modify: `src/web/index.html`
- Modify: `tests/unit/test_token_ui_contract.py`

**Interfaces:**
- Consumes: `tool_completed.metadata.draft_id`, approval challenge/confirm APIs, and the approval send API.
- Produces: an email preview card with recipient, subject, body, attachment hashes, expiry, confirm button, cancel button, and final receipt state.

- [ ] **Step 1: Write a failing UI contract test**

Assert the page contains `queueEmailApproval`, `renderEmailApprovalCard`, `confirmAndSendEmail`, the three approval endpoint paths, and distinct copy for `sent` and `unknown`.

- [ ] **Step 2: Run the UI contract test and verify RED**

Run:

```powershell
& '.venv\Scripts\python.exe' -m pytest -p no:cacheprovider tests/unit/test_token_ui_contract.py -q
```

Expected: the new UI contract assertions fail.

- [ ] **Step 3: Implement the card and state transitions**

On a successful `email_create_draft` event, queue its safe metadata. After the `done` event supplies `session_id`, create a challenge and render the full immutable preview. The confirm button must confirm the approval, call the send endpoint once with a generated idempotency key, disable itself while pending, and render:

- `邮件已成功发送` only for `status="sent"` and `external_delivery=true`.
- `发送结果待核验，请勿重复发送` for `status="unknown"`.
- A safe error message for rejection or transport failure.

The cancel button must revoke the approval and never call the send endpoint.

- [ ] **Step 4: Run UI and email regression tests**

Run:

```powershell
& '.venv\Scripts\python.exe' -m pytest -p no:cacheprovider tests/unit/test_token_ui_contract.py tests/unit/test_email_tools.py tests/unit/test_email_approval.py tests/integration/test_api.py tests/integration/test_application.py -q
```

Expected: all selected tests pass without network access.

- [ ] **Step 5: Run the complete suite**

Run:

```powershell
& '.venv\Scripts\python.exe' -m pytest -p no:cacheprovider
```

Expected: all tests pass; no real SMTP or IMAP connections occur.
