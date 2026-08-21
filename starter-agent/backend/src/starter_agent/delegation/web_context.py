"""Bounded, child-only web observations built from restricted browser artifacts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from starter_agent.agent.token_counter import TokenCounter
from starter_agent.agent.tool_result_guard import GuardedToolResult, ToolResultGuard, redact_tool_result_content


_DROP_TAGS = re.compile(r"<(?:script|style|nav|footer|header|aside)[^>]*>.*?</(?:script|style|nav|footer|header|aside)\s*>", re.IGNORECASE | re.DOTALL)
_COOKIE = re.compile(r"<[^>]*(?:cookie|consent|privacy-banner)[^>]*>.*?</[^>]+>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class GovernedWebResult:
    artifact: GuardedToolResult
    observation: GuardedToolResult
    dom_hash: str

    def __iter__(self):
        yield self.artifact
        yield self.observation

    def __iter__(self):
        yield self.artifact
        yield self.observation


class WebContextGovernor:
    """Preserve redacted raw browser output as an artifact, never as a model observation."""

    def __init__(self, counter: TokenCounter, max_result_tokens: int) -> None:
        self._counter = counter
        self._max_result_tokens = max_result_tokens
        self._seen_dom_hashes: set[str] = set()

    def govern(
        self,
        raw_content: str,
        *,
        tool_name: str,
        tool_call_id: str,
        raw_source_ref: str,
        seen_dom_hashes: set[str] | None = None,
    ) -> GovernedWebResult:
        artifact = ToolResultGuard(self._counter, max(self._max_result_tokens, 100_000)).guard(
            raw_content, tool_name, tool_call_id, raw_source_ref
        )
        cleaned = _clean_snapshot(raw_content)
        dom_hash = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()
        seen = self._seen_dom_hashes if seen_dom_hashes is None else seen_dom_hashes
        metadata = _metadata(raw_content, raw_source_ref, dom_hash)
        if dom_hash in seen:
            observation_value: dict[str, Any] = {
                "ok": True,
                "data": {"duplicate_observation": True},
                "metadata": metadata,
            }
        else:
            seen.add(dom_hash)
            observation_value = {
                "ok": True,
                "data": {"jd_observation": cleaned},
                "metadata": metadata,
            }
        observation = ToolResultGuard(self._counter, self._max_result_tokens).guard(
            json.dumps(observation_value, ensure_ascii=False), tool_name, tool_call_id, raw_source_ref
        )
        if (
            "jd_observation" not in observation.content
            and not observation_value["data"].get("duplicate_observation")
        ):
            # The generic guard cannot know that the JD body is the only field
            # worth retaining.  Shrink that field deterministically before its
            # own token-budget logic takes over.
            text = cleaned
            while text:
                observation_value["data"] = {"jd_observation": text}
                candidate = ToolResultGuard(self._counter, self._max_result_tokens).guard(
                    json.dumps(observation_value, ensure_ascii=False), tool_name, tool_call_id, raw_source_ref
                )
                if "jd_observation" in candidate.content:
                    observation = candidate
                    break
                if len(text) <= 1:
                    break
                text = text[: max(1, int(len(text) * 0.65))]
        return GovernedWebResult(artifact=artifact, observation=observation, dom_hash=dom_hash)

    @staticmethod
    def parent_payload(*, jobs: list[dict[str, Any]], missing: list[Any], errors: list[Any], usage: dict[str, Any], trace_ref: str, artifact_refs: list[str]) -> dict[str, Any]:
        """The only browser-derived shape allowed back into a Parent context."""
        return {
            "jobs": jobs,
            "missing": missing,
            "errors": errors,
            "usage": usage,
            "trace_ref": trace_ref,
            "artifact_refs": artifact_refs,
        }


def _metadata(raw_content: str, raw_source_ref: str, dom_hash: str) -> dict[str, Any]:
    try:
        value = json.loads(raw_content)
    except json.JSONDecodeError:
        value = {}
    source = value.get("metadata") if isinstance(value, dict) else {}
    source = source if isinstance(source, dict) else {}
    metadata: dict[str, Any] = {"raw_source_ref": raw_source_ref, "dom_hash": dom_hash, "is_untrusted_external_content": True}
    for key in ("source_url", "requested_url", "final_url", "source_content_sha256", "artifact_ref"):
        if isinstance(source.get(key), str) and source[key]:
            metadata[key] = source[key]
    return metadata


def _clean_snapshot(raw_content: str) -> str:
    try:
        value = json.loads(raw_content)
    except json.JSONDecodeError:
        value = raw_content
    html = _find_html(value)
    text = _DROP_TAGS.sub(" ", html)
    text = _COOKIE.sub(" ", text)
    text = _TAG.sub(" ", text)
    text = _SPACE.sub(" ", text).strip()
    # Persisted artifacts still receive the repository's shared secret redactor.
    return redact_tool_result_content(text)


def _find_html(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("html", "snapshot", "content", "body", "text"):
            candidate = value.get(key)
            if isinstance(candidate, str):
                return candidate
        for candidate in value.values():
            found = _find_html(candidate)
            if found:
                return found
    if isinstance(value, list):
        for candidate in value:
            found = _find_html(candidate)
            if found:
                return found
    return ""
