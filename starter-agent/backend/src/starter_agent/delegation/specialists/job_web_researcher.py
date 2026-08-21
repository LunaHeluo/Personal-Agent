from __future__ import annotations

import hashlib
import inspect
import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic import AnyHttpUrl
from typing import Literal

from starter_agent.delegation.context import RunContext
from starter_agent.delegation.models import RunOutcome, RunSpec
from starter_agent.job_research.candidates import rank_job_candidates
from starter_agent.job_research.company_attribution import preferred_company_attribution
from starter_agent.job_research.validation import validate_job
from starter_agent.delegation.specialists.job_web_error_policy import (
    JobWebErrorPolicy,
    WebErrorKind,
)


_HARD_MAX_PAGES = 10
_HARD_MAX_STEPS = 30
_HARD_PAGE_TIMEOUT_SECONDS = 35
_NAVIGATE = "mcp__playwright__browser_navigate"
_WAIT = "mcp__playwright__browser_wait_for"
_SNAPSHOT = "mcp__playwright__browser_snapshot"
_CLICK = "mcp__playwright__browser_click"


def _positive_limit(source: Mapping[str, Any], key: str, default: int) -> int:
    value = source.get(key, default)
    return int(value) if isinstance(value, (int, float)) and value > 0 else default


@dataclass(frozen=True, slots=True)
class WebResearchLimits:
    max_pages: int
    max_steps: int
    per_page_timeout_seconds: int

    @classmethod
    def resolve(
        cls,
        *,
        contract: Mapping[str, Any],
        registry: Mapping[str, Any] | None = None,
        policy: Mapping[str, Any] | None = None,
        parent: Mapping[str, Any] | None = None,
    ) -> "WebResearchLimits":
        registry = registry or {}
        policy = policy or {}
        parent = parent or {}
        return cls(
            max_pages=min(
                _HARD_MAX_PAGES,
                *(_positive_limit(item, "max_pages", _HARD_MAX_PAGES) for item in (contract, registry, policy, parent)),
            ),
            max_steps=min(
                _HARD_MAX_STEPS,
                *(_positive_limit(item, "max_steps", _HARD_MAX_STEPS) for item in (contract, registry, policy, parent)),
            ),
            per_page_timeout_seconds=min(
                _HARD_PAGE_TIMEOUT_SECONDS,
                *(
                    _positive_limit(item, "per_page_timeout_seconds", _HARD_PAGE_TIMEOUT_SECONDS)
                    for item in (contract, registry, policy, parent)
                ),
            ),
        )


class _Job(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None
    company: str | None
    location: str | None
    responsibilities: list[str]
    requirements: list[str]
    source_url: AnyHttpUrl
    final_url: AnyHttpUrl
    retrieved_at: datetime
    validation_state: Literal["verified", "partial_verified"]
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_refs: list[str]


class _JobProposal(BaseModel):
    """Model-owned JD fields; runtime provenance fields are ignored/rebuilt."""

    model_config = ConfigDict(extra="forbid")

    title: str | None
    company: str | None
    location: str | None
    responsibilities: list[str]
    requirements: list[str]
    source_url: AnyHttpUrl
    final_url: AnyHttpUrl
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_ref: str | None = None
    artifact_refs: list[str] = Field(default_factory=list)
    retrieved_at: datetime | None = None
    validation_state: Literal["verified", "partial_verified"] | None = None


class _ModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jobs: list[_JobProposal]
    missing: list[Any]
    errors: list[Any]
    # Some providers echo the requested envelope shape. Runtime always
    # discards this value and emits its own deterministic visited state.
    visited: Any = None


_JSON_FENCE = re.compile(
    r"\A\s*```(?:json)?\s*(?P<body>\{.*\})\s*```\s*\Z",
    re.IGNORECASE | re.DOTALL,
)


def _parse_model_output(content: str) -> _ModelOutput:
    """Normalize transport-only JSON fencing while preserving strict fields."""
    stripped = content.strip()
    fenced = _JSON_FENCE.match(stripped)
    if fenced is not None:
        stripped = fenced.group("body")
    return _ModelOutput.model_validate_json(stripped)


def _hydrate_model_jobs(
    proposals: list[_JobProposal], evidence: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Bind model semantics to runtime-owned retrieval metadata."""
    hydrated: list[dict[str, Any]] = []
    retrieved_at = datetime.now().astimezone()
    for proposal in proposals:
        raw = proposal.model_dump(mode="json")
        final_url = _canonical_url(str(raw["final_url"]))
        content_hash = str(raw["content_hash"])
        matched = next(
            (
                item
                for item in evidence
                if item["final_url"] == final_url
                and item["content_hash"] == content_hash
            ),
            None,
        )
        controlled = {
            key: raw[key]
            for key in (
                "title", "company", "location", "responsibilities",
                "requirements", "source_url", "final_url", "content_hash",
            )
        }
        controlled.update(
            {
                "retrieved_at": retrieved_at,
                "validation_state": "partial_verified",
                "artifact_refs": (
                    [matched["artifact_ref"]]
                    if matched is not None and matched.get("artifact_ref")
                    else []
                ),
            }
        )
        hydrated.append(_Job.model_validate(controlled).model_dump(mode="json"))
    return hydrated


@dataclass(frozen=True, slots=True)
class JobWebResearchResult:
    outcome: RunOutcome
    output: dict[str, Any]


class _Progress:
    def __init__(self, limits: WebResearchLimits, *, urls: list[str] | None = None, require_search: bool = False, failure_behavior: str = "allow_partial", redirect_validator=None, max_redirects: int = 5) -> None:
        self.limits = limits
        self.step_count = 0
        self.page_count = 0
        self.attempts: list[dict[str, Any]] = []
        self.states: list[str] = ["Candidates"]
        self._visited_urls: set[str] = set()
        self._navigation_epoch = 0
        self._allowed_urls = {_canonical_url(value) for value in (urls or [])}
        self._allowed_urls.discard("")
        self._candidate_urls = [
            value
            for value in (_canonical_url(item) for item in (urls or []))
            if value
        ]
        self.phase = "search" if require_search or not self._allowed_urls else "open"
        self.evidence: list[dict[str, Any]] = []
        self._current_requested_url = ""
        self._current_final_url = ""
        self.error_policy = JobWebErrorPolicy()
        self.failure_behavior = failure_behavior
        self.error_occurrences: dict[WebErrorKind, int] = {}
        self.consecutive_unrecoverable = 0
        self.handoff_error: str | None = None
        self.stop_reason: str | None = None
        self.redirect_validator = redirect_validator
        self.max_redirects = max_redirects
        self.sleeper = None
        self._forbidden_urls: set[str] = set()
        self.accepted_jobs: list[dict[str, Any]] = []
        self.forced_tool: str | None = None
        self.forced_url: str | None = None
        self.preflight_corrections = 0

    def checkpoint(self) -> dict[str, Any]:
        return {
            "phase": self.phase, "step_count": self.step_count,
            "page_count": self.page_count, "attempts": list(self.attempts),
            "states": list(self.states), "visited_urls": sorted(self._visited_urls),
            "allowed_urls": sorted(self._allowed_urls),
            "candidate_urls": list(self._candidate_urls),
            "current_requested_url": self._current_requested_url,
            "current_final_url": self._current_final_url,
            "error_occurrences": {key.value: value for key, value in self.error_occurrences.items()},
            "consecutive_unrecoverable": self.consecutive_unrecoverable,
            "evidence": list(self.evidence),
            "accepted_jobs": list(self.accepted_jobs),
            "preflight_corrections": self.preflight_corrections,
        }

    def restore(self, value: Mapping[str, Any]) -> None:
        self.phase = str(value.get("phase") or self.phase)
        self.step_count = int(value.get("step_count") or 0)
        self.page_count = int(value.get("page_count") or 0)
        self.attempts = list(value.get("attempts") or [])
        self.states = list(value.get("states") or self.states)
        self._visited_urls = set(value.get("visited_urls") or [])
        self._allowed_urls.update(value.get("allowed_urls") or [])
        restored_candidates = [
            _canonical_url(str(item)) for item in value.get("candidate_urls") or []
        ]
        self._candidate_urls = [item for item in restored_candidates if item]
        if not self._candidate_urls:
            self._candidate_urls = sorted(self._allowed_urls)
        self._current_requested_url = str(value.get("current_requested_url") or "")
        self._current_final_url = str(value.get("current_final_url") or "")
        self.error_occurrences = {WebErrorKind(key): int(count) for key, count in dict(value.get("error_occurrences") or {}).items()}
        self.consecutive_unrecoverable = int(value.get("consecutive_unrecoverable") or 0)
        self.evidence = list(value.get("evidence") or [])
        candidate_jobs = list(value.get("accepted_jobs") or [])
        bound, errors = _bind_jobs_to_evidence(candidate_jobs, self.evidence)
        normalized, _missing = normalize_jobs(bound, evidence=self.evidence)
        self.accepted_jobs = normalized if not errors else []
        self.preflight_corrections = int(value.get("preflight_corrections") or 0)

    def _correction(
        self, reason: str, details: dict[str, Any]
    ) -> tuple[str, str, dict[str, Any]]:
        self.preflight_corrections += 1
        if self.preflight_corrections > 2:
            return ("stop", "web_transition_recovery_exhausted", details)
        return ("skip", reason, details)

    def _required_transition(self, allowed: set[str]) -> dict[str, Any]:
        details: dict[str, Any] = {
            "current_phase": self.phase,
            "required_tools": sorted(allowed),
        }
        if self.phase == "open":
            candidate = next(
                (
                    item
                    for item in self._candidate_urls
                    if item not in self._visited_urls
                    and item not in self._forbidden_urls
                ),
                None,
            )
            if candidate is not None:
                details.update(
                    {
                        "required_tool": _NAVIGATE,
                        "required_arguments": {"url": candidate},
                    }
                )
        elif self.phase == "wait":
            details.update(
                {"required_tool": _WAIT, "required_arguments": {"time": 1}}
            )
        elif self.phase == "locate":
            details.update(
                {"required_tool": _SNAPSHOT, "required_arguments": {}}
            )
        return details

    def preflight(
        self, call: Any, context: RunContext
    ) -> tuple[str, str] | tuple[str, str, dict[str, Any]] | None:
        if context.refresh_cancellation():
            return ("stop", "cancelled")
        if self.step_count >= self.limits.max_steps:
            return ("stop", "step_limit")
        if self.forced_tool is not None:
            if call.name != self.forced_tool:
                # Keep the recovery constraint armed and return a
                # zero-side-effect Observation so the bounded model loop can
                # correct its next call. A single planning miss must not throw
                # away evidence already collected from earlier candidates.
                details: dict[str, Any] = {"required_tool": self.forced_tool}
                if self.forced_url is not None:
                    details["required_arguments"] = {"url": self.forced_url}
                return self._correction("recovery_action_required", details)
            if self.forced_url is not None and _canonical_url(str(call.arguments.get("url") or "")) != self.forced_url:
                return self._correction(
                    "recovery_action_required",
                    {
                        "required_tool": self.forced_tool,
                        "required_arguments": {"url": self.forced_url},
                    },
                )
            self.forced_tool = None
            self.forced_url = None
        if call.name != _NAVIGATE:
            allowed = {
                "search": {"search_jobs_serpapi"}, "open": {_NAVIGATE},
                "wait": {_WAIT}, "locate": {_SNAPSHOT},
                "completeness": {_CLICK, _NAVIGATE, _SNAPSHOT},
            }
            if call.name in allowed.get(self.phase, set()):
                return None
            # Search-first remains a hard boundary.  After a page is opened,
            # an out-of-order read is returned as a zero-side-effect
            # Observation so the bounded model loop can correct its next step.
            details = self._required_transition(allowed.get(self.phase, set()))
            if self.phase == "search":
                return ("stop", "invalid_web_transition", details)
            return self._correction("invalid_web_transition", details)
        if self.phase not in {"open", "completeness"}:
            return ("stop", "invalid_web_transition")
        requested = _canonical_url(str(call.arguments.get("url") or ""))
        if requested in self._forbidden_urls:
            return ("stop", "candidate_url_forbidden")
        if requested not in self._allowed_urls:
            return ("stop", "candidate_url_not_allowed")
        if requested in self._visited_urls:
            self.attempts.append({
                "attempt": len(self.attempts) + 1,
                "requested_url": requested,
                "final_url": requested,
                "status": "duplicate",
            })
            return ("skip", "duplicate_page")
        if self.page_count >= self.limits.max_pages:
            return ("stop", "page_limit")
        self._navigation_epoch += 1
        return None

    def repeat_scope(self, call: Any, _context: RunContext) -> str:
        return (
            f"browser-page:{self._navigation_epoch}"
            if call.name.startswith("mcp__playwright__browser_")
            else "run"
        )

    async def tool_event(self, event: dict[str, Any]) -> None:
        if event.get("type") != "tool_completed":
            return
        self.preflight_corrections = 0
        self.step_count += 1
        name = str(event.get("name") or "")
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        if name == _NAVIGATE:
            observed_requested = _canonical_url(str(event.get("requested_url") or metadata.get("requested_url") or ""))
            if observed_requested:
                self._current_requested_url = observed_requested
        state = {
            "search_jobs_serpapi": "Candidates",
            _NAVIGATE: "OpenPage",
            _WAIT: "WaitRender",
            _SNAPSHOT: "LocateBody",
            _CLICK: "ExpandOrDetail",
        }.get(name)
        if state:
            self.states.append(state)
        if name == "search_jobs_serpapi":
            candidates = [
                value
                for value in (
                    _canonical_url(str(item))
                    for item in event.get("candidate_urls", [])
                )
                if value
            ]
            self._allowed_urls.update(candidates)
            self._candidate_urls.extend(
                value for value in candidates if value not in self._candidate_urls
            )
            self.phase = "open"
        elif name == _NAVIGATE: self.phase = "wait"
        elif name == _WAIT: self.phase = "locate"
        elif name == _SNAPSHOT: self.phase = "completeness"
        elif name == _CLICK: self.phase = "locate"
        if name == _SNAPSHOT and event.get("ok"):
            evidence = {
                "requested_url": self._current_requested_url,
                "final_url": _canonical_url(str(event.get("final_url") or event.get("source_url") or self._current_final_url)),
                "content_hash": str(event.get("source_content_sha256") or ""),
                "artifact_ref": str(event.get("artifact_ref") or event.get("raw_source_ref") or ""),
            }
            self.evidence.append(evidence)
            structured_job = event.get("structured_job")
            if isinstance(structured_job, dict) and all(
                key in structured_job for key in ("source_url", "final_url", "content_hash", "artifact_refs")
            ):
                bound, errors = _bind_jobs_to_evidence([structured_job], [evidence])
                normalized, _missing = normalize_jobs(bound, evidence=[evidence])
                if not errors and normalized:
                    self.accepted_jobs = _deduplicate_jobs([*self.accepted_jobs, *normalized])
        if not event.get("ok"):
            classified = self.error_policy.classify(
                error_code=event.get("error_code"),
                status_code=event.get("status_code") or metadata.get("status_code"),
            )
            occurrence = self.error_occurrences.get(classified.kind, 0) + 1
            self.error_occurrences[classified.kind] = occurrence
            decision = self.error_policy.decide(
                classified.kind, occurrence=occurrence,
                consecutive_unrecoverable=self.consecutive_unrecoverable,
                failure_behavior=self.failure_behavior,
            )
            # A generic tool timeout is classified as a render timeout by the
            # shared web policy, but Search has no rendered page to wait for.
            # If the contract already supplied seed URLs, continue with those;
            # otherwise retry Search itself within the policy's bounded count.
            search_seed_fallback = name == "search_jobs_serpapi" and bool(self._allowed_urls)
            if name == "search_jobs_serpapi" and decision.action == "retry_wait":
                decision = decision.__class__("retry", retryable=True)
            elif name == _NAVIGATE and decision.action == "retry_wait":
                decision = decision.__class__("retry", retryable=True)
            elif name == _SNAPSHOT and decision.action == "retry_wait":
                decision = decision.__class__("retry_snapshot", retryable=True)
            self.attempts.append({
                "attempt": len(self.attempts) + 1,
                "requested_url": _canonical_url(str(event.get("requested_url") or self._current_requested_url)),
                "final_url": _canonical_url(str(event.get("final_url") or "")),
                "status": "failed", "error_code": classified.code,
                "recovery_action": (
                    "fallback_seed_candidates" if search_seed_fallback else decision.action
                ),
            })
            if search_seed_fallback:
                self.phase = "open"
                return
            if name == "search_jobs_serpapi":
                self.phase = "search"
            if decision.action in {"next_candidate", "partial", "stop"}:
                self.consecutive_unrecoverable += 1
                if self.consecutive_unrecoverable >= 3:
                    decision = decision.__class__("stop")
            requested_error_url = _canonical_url(str(event.get("requested_url") or self._current_requested_url))
            if decision.action == "next_candidate" and requested_error_url:
                self._forbidden_urls.add(requested_error_url)
            if decision.delay_seconds and self.sleeper is not None:
                waited = self.sleeper(decision.delay_seconds)
                if inspect.isawaitable(waited):
                    await waited
            if decision.action == "retry":
                self.forced_tool, self.forced_url = name, requested_error_url or None
            elif decision.action == "retry_wait":
                self.forced_tool, self.forced_url = _WAIT, None
            elif decision.action == "retry_snapshot":
                self.forced_tool, self.forced_url = _SNAPSHOT, None
            if decision.action == "wait_for_user":
                self.handoff_error = classified.code
                self.states.append("WaitingUser")
            elif decision.action in {"partial", "stop"}:
                self.handoff_error = classified.code
                self.stop_reason = "access_blocked" if decision.action == "partial" else "candidate_recovery_exhausted"
            elif decision.action == "next_candidate" and not (self._allowed_urls - self._forbidden_urls):
                self.stop_reason = "candidate_recovery_exhausted"
            if decision.action == "next_candidate":
                self.phase = "open"
            elif name == _NAVIGATE:
                self.phase = "open"
            elif name == _WAIT:
                self.phase = "wait"
            elif name == _SNAPSHOT:
                self.phase = "locate"
            return
        if name != _NAVIGATE:
            return
        requested = _canonical_url(str(event.get("requested_url") or ""))
        final = _canonical_url(str(event.get("final_url") or requested))
        chain = event.get("metadata", {}).get("redirect_chain") or event.get("metadata", {}).get("redirect_hops")
        if requested and final and urlsplit(requested).netloc != urlsplit(final).netloc:
            if not isinstance(chain, list) or not chain:
                self.stop_reason = "redirect_chain_unverifiable"
                return
            canonical_chain = [_canonical_url(str(value)) for value in chain]
            if len(canonical_chain) > self.max_redirects or any(not value for value in canonical_chain):
                self.stop_reason = "redirect_limit"
                return
            if self.redirect_validator is None:
                self.stop_reason = "redirect_chain_unverifiable"
                return
            try:
                await self.redirect_validator(canonical_chain)
            except Exception:
                self.stop_reason = "redirect_chain_denied"
                return
        self._current_requested_url = requested
        self._current_final_url = final
        duplicate = bool({requested, final} & self._visited_urls)
        if not duplicate:
            self.page_count += 1
            self._visited_urls.update(value for value in (requested, final) if value)
        self.attempts.append(
            {
                "attempt": len(self.attempts) + 1,
                "requested_url": requested,
                "final_url": final,
                "status": "duplicate" if duplicate else ("opened" if event.get("ok") else "failed"),
            }
        )

    def stop_probe(self, context: RunContext) -> str | None:
        if context.refresh_cancellation():
            return "cancelled"
        return self.stop_reason


class JobWebResearcher:
    """Contract adapter around the shared Runtime; it is not an Agent loop."""

    def __init__(self, runtime, *, checkpoint_sink=None, sleeper=None, artifact_sink=None) -> None:
        self.runtime = runtime
        self.checkpoint_sink = checkpoint_sink
        self.sleeper = sleeper or asyncio.sleep
        self.artifact_sink = artifact_sink

    async def run(
        self,
        spec: RunSpec,
        context: RunContext,
        inputs: Mapping[str, Any],
        *,
        registry_limits: Mapping[str, Any] | None = None,
        policy_limits: Mapping[str, Any] | None = None,
        parent_limits: Mapping[str, Any] | None = None,
    ) -> JobWebResearchResult:
        supplied_parent_limits = parent_limits or context.working_memory
        parent_limits = {
            "max_pages": supplied_parent_limits.get(
                "max_pages", supplied_parent_limits.get("policy_max_pages")
            ),
            "max_steps": supplied_parent_limits.get(
                "max_steps", supplied_parent_limits.get("policy_max_steps")
            ),
            "per_page_timeout_seconds": supplied_parent_limits.get(
                "per_page_timeout_seconds",
                supplied_parent_limits.get("policy_per_page_timeout_seconds"),
            ),
        }
        limits = WebResearchLimits.resolve(
            contract=inputs,
            registry=registry_limits,
            policy=policy_limits,
            parent=parent_limits,
        )
        redirect_validator = getattr(getattr(self.runtime, "gate", None), "browser_policy", None)
        progress = _Progress(
            limits, urls=list(inputs.get("urls") or []),
            require_search=bool(inputs.get("require_search", False)),
            failure_behavior=str(inputs.get("failure_behavior") or "allow_partial"),
            redirect_validator=(None if redirect_validator is None else redirect_validator.validate_all),
            max_redirects=min(5, _positive_limit(inputs, "max_redirects", 5)),
        )
        async def bounded_sleep(seconds: float) -> None:
            if context.refresh_cancellation():
                raise asyncio.CancelledError()
            if context.deadline_at is not None:
                remaining = (context.deadline_at - datetime.now(context.deadline_at.tzinfo)).total_seconds()
                if remaining <= 0 or seconds > remaining:
                    raise TimeoutError("job web retry deadline exhausted")
            waited = self.sleeper(seconds)
            if inspect.isawaitable(waited):
                await waited
            if context.refresh_cancellation():
                raise asyncio.CancelledError()
        progress.sleeper = bounded_sleep
        saved_progress = context.working_memory.get("job_web_progress")
        if isinstance(saved_progress, dict):
            progress.restore(saved_progress)
        if context.refresh_cancellation():
            outcome = RunOutcome(
                disposition="cancelled", run_id=spec.run_id,
                status="cancelled", error_code="run_cancelled",
            )
            return JobWebResearchResult(outcome, self._empty(progress, "cancelled"))

        previous = (
            context.per_tool_timeout_seconds,
            context.boundary_stop_probe,
            context.tool_preflight_probe,
            context.repeated_call_scope_probe,
            context.boundary_stop_reason,
            context.max_tool_calls_per_response,
            context.suspension_probe,
            context.suspension_requested,
            context.suspension_checkpoint_ref,
        )
        context.per_tool_timeout_seconds = limits.per_page_timeout_seconds
        context.boundary_stop_probe = progress.stop_probe
        context.tool_preflight_probe = progress.preflight
        context.repeated_call_scope_probe = progress.repeat_scope
        context.boundary_stop_reason = None
        context.max_tool_calls_per_response = 1
        def suspend_for_handoff(current: RunContext) -> str | None:
            if progress.handoff_error is None or progress.failure_behavior != "wait_for_user":
                return None
            checkpoint = progress.error_policy.create_handoff_checkpoint(
                parent_run_id=current.parent_run_id,
                child_task_id=current.child_task_id or "",
                child_run_id=current.run_id,
                principal=current.principal,
                requested_url=progress._current_requested_url,
                next_phase="open",
                created_at=datetime.now().astimezone().isoformat(),
            )
            current.working_memory["job_web_progress"] = progress.checkpoint()
            checkpoint["run_context"] = current.to_checkpoint()
            checkpoint["progress"] = progress.checkpoint()
            ref = f"checkpoint:job-web:{current.run_id}:{current.context_version}"
            current.working_memory["job_web_handoff"] = checkpoint
            if self.checkpoint_sink is not None:
                ref = self.checkpoint_sink(ref, checkpoint)
            return ref
        context.suspension_probe = suspend_for_handoff
        bounded_spec = spec.model_copy(update={"max_steps": min(spec.max_steps, limits.max_steps)})
        try:
            outcome = await self.runtime.run(
                spec=bounded_spec,
                context=context,
                on_tool_event=progress.tool_event,
                **({"on_tool_artifact": self.artifact_sink} if self.artifact_sink is not None else {}),
            )
            reason = context.boundary_stop_reason
        finally:
            (
                context.per_tool_timeout_seconds,
                context.boundary_stop_probe,
                context.tool_preflight_probe,
                context.repeated_call_scope_probe,
                context.boundary_stop_reason,
                context.max_tool_calls_per_response,
                context.suspension_probe,
                context.suspension_requested,
                context.suspension_checkpoint_ref,
            ) = previous
        if outcome.status == "waiting_children" and progress.handoff_error is not None:
            waiting = RunOutcome(
                disposition="suspended", run_id=spec.run_id,
                status="waiting_for_user", checkpoint_ref=outcome.checkpoint_ref,
            )
            output = self._empty(progress, "waiting_for_user")
            output["missing"].append({"reason": progress.handoff_error})
            output["errors"].append({"code": progress.handoff_error})
            return JobWebResearchResult(waiting, output)
        if progress.handoff_error is not None:
            partial = RunOutcome(
                disposition="completed", run_id=spec.run_id, status="partial",
                output_ref=f"context-output:{spec.run_id}:{context.context_version}",
            )
            output = self._empty(progress, "access_blocked")
            accepted, accepted_missing = normalize_jobs(progress.accepted_jobs, evidence=progress.evidence)
            output["jobs"] = accepted
            output["missing"].extend(accepted_missing)
            output["missing"].append({"reason": progress.handoff_error})
            output["errors"].append({"code": progress.handoff_error})
            return JobWebResearchResult(partial, output)
        if reason is not None:
            partial = RunOutcome(
                disposition="completed", run_id=spec.run_id, status="partial",
                output_ref=f"context-output:{spec.run_id}:{context.context_version}",
            )
            output = self._empty(progress, reason)
            accepted, accepted_missing = normalize_jobs(
                progress.accepted_jobs, evidence=progress.evidence
            )
            output["jobs"] = accepted
            output["missing"].extend(accepted_missing)
            output["missing"].append({"reason": reason})
            output["errors"].append({"code": reason})
            return JobWebResearchResult(partial, output)
        if outcome.status == "budget_exhausted":
            # Preserve the bounded run's concrete completed/unfinished report
            # in a Result Envelope. Returning the raw budget terminal status
            # would discard collected evidence and usage before Parent Merge.
            partial = RunOutcome(
                disposition="completed", run_id=spec.run_id, status="partial",
                output_ref=f"context-output:{spec.run_id}:{context.context_version}",
            )
            output = self._empty(progress, "budget_exhausted")
            accepted, accepted_missing = normalize_jobs(
                progress.accepted_jobs, evidence=progress.evidence
            )
            output["jobs"] = accepted
            output["missing"].extend(accepted_missing)
            output["missing"].append({"reason": "budget_exhausted"})
            output["errors"].append({"code": "runtime_budget_exceeded"})
            return JobWebResearchResult(partial, output)
        if outcome.status != "succeeded":
            reason = "deadline_exhausted" if outcome.status == "timed_out" else outcome.status
            return JobWebResearchResult(outcome, self._empty(progress, reason))
        if progress.phase != "completeness":
            failed = RunOutcome(
                disposition="failed", run_id=spec.run_id, status="failed",
                error_code="final_before_completeness",
            )
            return JobWebResearchResult(failed, self._empty(progress, "final_before_completeness"))

        try:
            parsed = _parse_model_output(context.output_buffer[-1])
            model_jobs = _hydrate_model_jobs(parsed.jobs, progress.evidence)
        except (IndexError, ValidationError, ValueError) as exc:
            partial = RunOutcome(
                disposition="completed", run_id=spec.run_id, status="partial",
                output_ref=f"context-output:{spec.run_id}:{context.context_version}",
            )
            output = self._empty(progress, "schema_invalid")
            failures = []
            if isinstance(exc, ValidationError):
                failures = [
                    {
                        "path": ".".join(str(item) for item in error.get("loc", ())),
                        "type": str(error.get("type") or "validation_error"),
                        "message": str(error.get("msg") or "invalid value")[:300],
                    }
                    for error in exc.errors(include_input=False)[:20]
                ]
            else:
                failures = [{"path": "", "type": type(exc).__name__, "message": "invalid JSON transport"}]
            output["missing"].append({"reason": "schema_invalid"})
            output["errors"].append(
                {"code": "job_web_output_schema_invalid", "failures": failures}
            )
            return JobWebResearchResult(partial, output)

        jobs, binding_errors = _bind_jobs_to_evidence(
            model_jobs, progress.evidence
        )
        jobs, deterministic_missing = normalize_jobs(jobs, evidence=progress.evidence)
        progress.accepted_jobs = jobs
        context.working_memory["job_web_progress"] = progress.checkpoint()
        target = _positive_limit(inputs, "target_valid_jobs", len(jobs) or 1)
        stop_reason = "target_reached" if len(jobs) >= target else "candidates_exhausted"
        progress.states.extend(("Extract", "Completeness", "Complete" if stop_reason == "target_reached" else "Partial"))
        return JobWebResearchResult(
            outcome,
            {
                "jobs": jobs,
                "missing": [*parsed.missing, *deterministic_missing],
                "errors": [*parsed.errors, *binding_errors],
                "visited": self._visited(progress, stop_reason),
            },
        )

    @classmethod
    def _empty(cls, progress: _Progress, reason: str) -> dict[str, Any]:
        return {
            "jobs": [], "missing": [], "errors": [],
            "visited": cls._visited(progress, reason),
        }

    @staticmethod
    def _visited(progress: _Progress, reason: str) -> dict[str, Any]:
        return {
            "page_count": progress.page_count,
            "step_count": progress.step_count,
            "attempts": progress.attempts,
            "states": progress.states,
            "stop_reason": reason,
        }


def _canonical_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
            return ""
        query = [
            (key, item) for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.casefold().startswith("utm_")
        ]
        return urlunsplit((parsed.scheme.casefold(), parsed.netloc.casefold(), parsed.path or "/", urlencode(query), ""))
    except ValueError:
        return ""


def _deduplicate_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for job in jobs:
        job["source_url"] = _canonical_url(job["source_url"])
        job["final_url"] = _canonical_url(job["final_url"])
        signature = hashlib.sha256(
            json.dumps(
                [job.get("title"), job.get("company"), job.get("location"), job.get("responsibilities"), job.get("requirements")],
                ensure_ascii=False, sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        identities = {job["source_url"], job["final_url"], job["content_hash"], signature}
        if seen & identities:
            continue
        seen.update(identities)
        result.append(job)
    return result


def normalize_jobs(
    jobs: list[dict[str, Any]], *, evidence: list[dict[str, Any]] | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply existing candidate classification before deterministic JD checks."""

    candidates = rank_job_candidates(
        [
            {
                "url": job["final_url"],
                "title": job.get("title") or "Untitled job",
                "company": job.get("company") or "",
                "company_source": "page_html" if job.get("company") else "",
                "company_confidence": "high" if job.get("company") else "",
                "location": job.get("location") or "",
                "snippet": " ".join(
                    [*job.get("responsibilities", []), *job.get("requirements", [])]
                ),
                "url_kind": "organic",
                "provider_position": index,
            }
            for index, job in enumerate(jobs, start=1)
        ],
        limit=len(jobs),
    )
    allowed_urls = {_canonical_url(candidate.url) for candidate in candidates}
    filtered = [job for job in jobs if _canonical_url(job["final_url"]) in allowed_urls]
    accepted: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for job in filtered:
        attribution = preferred_company_attribution(
            {
                "company": job.get("company") or "",
                "company_source": "page_html",
                "company_confidence": "high" if job.get("company") else "",
            }
        )
        job["company"] = attribution.company or None
        matched = _matching_evidence(job, evidence or [])
        selected_url = matched["requested_url"] if matched is not None else job["source_url"]
        validation = validate_job(job, selected_url)
        if validation.state == "rejected":
            continue
        accepted.append(job)
        job["validation_state"] = validation.state
        fields = [reason.removeprefix("missing_") for reason in validation.reason_codes if reason.startswith("missing_")]
        if fields: missing.append({"source_url": _canonical_url(job["source_url"]), "fields": fields})
    return _deduplicate_jobs(accepted), missing


def _bind_jobs_to_evidence(
    jobs: list[dict[str, Any]], evidence: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for job in jobs:
        matched = _matching_evidence(job, evidence)
        if matched is None:
            errors.append({"code": "job_evidence_unbound", "source_url": job["source_url"]})
            continue
        if _canonical_url(job["source_url"]) != matched["requested_url"]:
            errors.append({"code": "job_source_url_mismatch", "source_url": job["source_url"]})
            continue
        accepted.append(job)
    return accepted, errors


def _matching_evidence(
    job: dict[str, Any], evidence: list[dict[str, Any]]
) -> dict[str, Any] | None:
    return next(
        (
            item for item in evidence
            if item["final_url"] == _canonical_url(job["final_url"])
            and item["content_hash"] == job["content_hash"]
            and item["artifact_ref"] in job["artifact_refs"]
        ),
        None,
    )
