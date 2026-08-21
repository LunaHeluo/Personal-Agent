from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal


class WebErrorKind(str, Enum):
    LOAD_FAILURE = "load_failure"
    CONNECTION_FAILURE = "connection_failure"
    NOT_FOUND = "not_found"
    REDIRECT = "redirect"
    RENDER_TIMEOUT = "render_timeout"
    SELECTOR_FAILURE = "selector_failure"
    EMPTY_BODY = "empty_body"
    DUPLICATE = "duplicate"
    ACCESS_BLOCKED = "access_blocked"
    POLICY_DENIED_CANDIDATE = "policy_denied_candidate"
    BROWSER_CONTEXT_LOST = "browser_context_lost"


@dataclass(frozen=True, slots=True)
class ClassifiedWebError:
    kind: WebErrorKind
    code: str


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    action: Literal[
        "retry", "retry_wait", "retry_snapshot", "next_candidate",
        "deduplicate", "follow_redirect", "wait_for_user", "partial", "stop",
    ]
    delay_seconds: int = 0
    retryable: bool = False


_ERRORS = {
    "page_load_failed": ClassifiedWebError(WebErrorKind.LOAD_FAILURE, "job_web_load_failed"),
    "connection_error": ClassifiedWebError(WebErrorKind.CONNECTION_FAILURE, "job_web_connection_failed"),
    "redirect": ClassifiedWebError(WebErrorKind.REDIRECT, "job_web_redirect"),
    "tool_timeout": ClassifiedWebError(WebErrorKind.RENDER_TIMEOUT, "job_web_render_timeout"),
    "render_timeout": ClassifiedWebError(WebErrorKind.RENDER_TIMEOUT, "job_web_render_timeout"),
    "selector_not_found": ClassifiedWebError(WebErrorKind.SELECTOR_FAILURE, "job_web_selector_failed"),
    "empty_body": ClassifiedWebError(WebErrorKind.EMPTY_BODY, "job_web_empty_body"),
    "duplicate_page": ClassifiedWebError(WebErrorKind.DUPLICATE, "job_web_duplicate"),
    "browser_no_open_page": ClassifiedWebError(
        WebErrorKind.BROWSER_CONTEXT_LOST,
        "job_web_browser_context_lost",
    ),
}
_ACCESS_ERRORS = {"login_required", "captcha", "permission_denied", "robots_denied", "site_denied"}
_CANDIDATE_POLICY_ERRORS = {
    "sensitive_url_query",
    "unsafe_url",
    "unsafe_redirect",
    "browser_payload",
    "browser_script",
    "forbidden_action",
}


class JobWebErrorPolicy:
    def __init__(self, *, max_retry_delay_seconds: int = 4, max_redirects: int = 5, handoff_timeout_seconds: int = 900) -> None:
        if min(max_retry_delay_seconds, max_redirects, handoff_timeout_seconds) < 1:
            raise ValueError("job web policy limits must be positive")
        self.max_retry_delay_seconds = max_retry_delay_seconds
        self.max_redirects = max_redirects
        self.handoff_timeout_seconds = handoff_timeout_seconds

    def classify(self, *, error_code: str | None = None, status_code: int | None = None) -> ClassifiedWebError:
        if status_code in {404, 410}:
            return ClassifiedWebError(WebErrorKind.NOT_FOUND, "job_web_not_found")
        if error_code in _ACCESS_ERRORS:
            return ClassifiedWebError(WebErrorKind.ACCESS_BLOCKED, f"job_web_{error_code}")
        if error_code in _CANDIDATE_POLICY_ERRORS:
            return ClassifiedWebError(
                WebErrorKind.POLICY_DENIED_CANDIDATE,
                "job_web_candidate_policy_denied",
            )
        return _ERRORS.get(error_code or "", ClassifiedWebError(WebErrorKind.LOAD_FAILURE, "job_web_load_failed"))

    def decide(self, kind: WebErrorKind, *, occurrence: int, consecutive_unrecoverable: int = 0, failure_behavior: str = "allow_partial") -> RecoveryDecision:
        if occurrence < 1:
            raise ValueError("occurrence must be positive")
        if consecutive_unrecoverable >= 3:
            return RecoveryDecision("stop")
        if kind is WebErrorKind.ACCESS_BLOCKED:
            return RecoveryDecision("wait_for_user" if failure_behavior == "wait_for_user" else "partial")
        if kind is WebErrorKind.NOT_FOUND:
            return RecoveryDecision("next_candidate")
        if kind is WebErrorKind.POLICY_DENIED_CANDIDATE:
            return RecoveryDecision("next_candidate")
        if kind is WebErrorKind.BROWSER_CONTEXT_LOST:
            return RecoveryDecision("next_candidate")
        if kind is WebErrorKind.DUPLICATE:
            return RecoveryDecision("deduplicate")
        if kind is WebErrorKind.REDIRECT:
            return RecoveryDecision("follow_redirect" if occurrence <= self.max_redirects else "stop")
        if kind is WebErrorKind.EMPTY_BODY:
            return RecoveryDecision("retry_snapshot", retryable=True) if occurrence == 1 else RecoveryDecision("next_candidate")
        if kind is WebErrorKind.RENDER_TIMEOUT:
            return RecoveryDecision("retry_wait", retryable=True) if occurrence <= 2 else RecoveryDecision("next_candidate")
        if kind is WebErrorKind.SELECTOR_FAILURE:
            return RecoveryDecision("retry_snapshot", retryable=True) if occurrence <= 2 else RecoveryDecision("partial")
        if occurrence <= 2:
            return RecoveryDecision("retry", min(2 ** (occurrence - 1), self.max_retry_delay_seconds), True)
        return RecoveryDecision("next_candidate")

    def create_handoff_checkpoint(self, *, parent_run_id: str, child_task_id: str, child_run_id: str, principal: str, requested_url: str, next_phase: str, created_at: str) -> dict[str, str]:
        return {
            "version": "job-web-handoff-v1", "parent_run_id": parent_run_id,
            "child_task_id": child_task_id, "child_run_id": child_run_id,
            "principal": principal, "requested_url": requested_url,
            "next_phase": next_phase, "created_at": created_at,
        }

    def resume_handoff(self, checkpoint: dict[str, str], *, parent_run_id: str, child_task_id: str, child_run_id: str, principal: str, now: str) -> dict[str, str]:
        expected = {
            "parent_run_id": parent_run_id, "child_task_id": child_task_id,
            "child_run_id": child_run_id, "principal": principal,
        }
        if checkpoint.get("version") != "job-web-handoff-v1" or any(checkpoint.get(key) != value for key, value in expected.items()):
            raise ValueError("handoff_checkpoint_authority_mismatch")
        elapsed = (datetime.fromisoformat(now) - datetime.fromisoformat(checkpoint["created_at"])).total_seconds()
        if elapsed > self.handoff_timeout_seconds:
            raise TimeoutError("job_web_handoff_timeout")
        return dict(checkpoint)
