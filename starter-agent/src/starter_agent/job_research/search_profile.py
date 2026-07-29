from __future__ import annotations

import json
import re
from dataclasses import dataclass
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError, field_validator

from starter_agent.capabilities.policy import (
    ScopeDenied,
    validate_serpapi_payload,
)
from starter_agent.domain.models import Message
from starter_agent.knowledge.models import Evidence
from starter_agent.providers.base import Provider


_FENCED_JSON = re.compile(
    r"\A```(?:json)?[ \t]*\r?\n(?P<body>.*)\r?\n```[ \t]*\Z",
    re.IGNORECASE | re.DOTALL,
)

_RETRY_EXAMPLE = {
    "query": "Python backend engineer",
    "location": "深圳",
    "evidence_refs": ["E1"],
    "explicit_freshness": False,
}

_PERSON_BEFORE_HISTORY = re.compile(
    r"\b(?P<person>[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+"
    r"(?:worked|works|was\s+employed|is\s+employed)\b"
)
_ORGANIZATION_AFTER_HISTORY = re.compile(
    r"\b(?:at|for|with|joined)\s+(?P<organization>[A-Z][A-Za-z0-9&.-]+)\b"
)


class SearchProfileUnavailable(RuntimeError):
    def __init__(
        self,
        code: str,
        attempts: tuple["ProfileAttemptSummary", ...] = (),
    ) -> None:
        super().__init__(code)
        self.code = code
        self.attempts = attempts


@dataclass(frozen=True, slots=True)
class ProfileAttemptSummary:
    attempt: int
    model_request_id: str
    output_length: int
    fields: tuple[str, ...]
    error_code: str | None


@dataclass(frozen=True, slots=True)
class PublicJobSearchProfile:
    query: str
    location: str | None
    evidence_refs: tuple[str, ...]
    explicit_freshness: bool = False
    attempts: tuple[ProfileAttemptSummary, ...] = ()

    @property
    def role_terms(self) -> tuple[str, ...]:
        return tuple(re.findall(r"[\w+#.-]+", self.query, re.UNICODE))


class _GeneratedProfile(BaseModel):
    query: str = Field(min_length=2, max_length=60)
    location: str | None = Field(default=None, max_length=80)
    evidence_refs: list[str] = Field(min_length=1, max_length=10)
    explicit_freshness: bool = False

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("location")
    @classmethod
    def normalize_location(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None


class JobSearchProfileBuilder:
    async def build(
        self,
        *,
        user_request: str,
        evidence: list[Evidence],
        provider: Provider,
        model: str,
    ) -> PublicJobSearchProfile:
        if not evidence:
            raise SearchProfileUnavailable("no_resume_evidence")
        allowed_refs = {item.evidence_id for item in evidence}
        messages = self._messages(user_request, evidence)
        last_code = "invalid_search_profile"
        attempts: list[ProfileAttemptSummary] = []
        for attempt in range(2):
            model_request_id = f"job-search-profile-{uuid4().hex}"
            response = await provider.complete(messages, model, tools=[])
            content = response.content or ""
            try:
                generated = self._parse(content)
                if not set(generated.evidence_refs).issubset(allowed_refs):
                    raise SearchProfileUnavailable("invalid_evidence_reference")
                arguments: dict[str, object] = {"query": generated.query}
                if generated.location:
                    arguments["location"] = generated.location
                referenced = [
                    item
                    for item in evidence
                    if item.evidence_id in generated.evidence_refs
                ]
                validate_serpapi_payload(
                    arguments,
                    (),
                    sensitive_terms=self._sensitive_terms(referenced),
                )
            except SearchProfileUnavailable as exc:
                last_code = exc.code
            except (ValidationError, ValueError, json.JSONDecodeError) as exc:
                last_code = self._structure_error_code(exc)
            except ScopeDenied:
                last_code = "unsafe_search_profile"
            else:
                attempts.append(
                    ProfileAttemptSummary(
                        attempt=attempt + 1,
                        model_request_id=model_request_id,
                        output_length=len(content),
                        fields=self._json_fields(content),
                        error_code=None,
                    )
                )
                return PublicJobSearchProfile(
                    query=generated.query,
                    location=generated.location,
                    evidence_refs=tuple(generated.evidence_refs),
                    explicit_freshness=generated.explicit_freshness,
                    attempts=tuple(attempts),
                )
            attempts.append(
                ProfileAttemptSummary(
                    attempt=attempt + 1,
                    model_request_id=model_request_id,
                    output_length=len(content),
                    fields=self._json_fields(content),
                    error_code=last_code,
                )
            )
            if attempt == 0:
                messages = self._messages(
                    user_request,
                    evidence,
                    retry_code=last_code,
                )
        raise SearchProfileUnavailable(last_code, tuple(attempts))

    @staticmethod
    def _structure_error_code(exc: Exception) -> str:
        if isinstance(exc, ValidationError):
            if any(item["type"] == "json_invalid" for item in exc.errors()):
                return "invalid_json"
            return "schema_validation_failed"
        if isinstance(exc, json.JSONDecodeError):
            return "invalid_json"
        return "schema_validation_failed"

    @staticmethod
    def _parse(content: str) -> _GeneratedProfile:
        stripped = content.strip()
        match = _FENCED_JSON.fullmatch(stripped)
        if match:
            stripped = match.group("body").strip()
        return _GeneratedProfile.model_validate_json(stripped)

    @staticmethod
    def _json_fields(content: str) -> tuple[str, ...]:
        stripped = content.strip()
        match = _FENCED_JSON.fullmatch(stripped)
        if match:
            stripped = match.group("body").strip()
        try:
            payload = json.loads(stripped)
        except (json.JSONDecodeError, TypeError, ValueError):
            return ()
        if not isinstance(payload, dict):
            return ()
        return tuple(sorted(str(key) for key in payload))

    @staticmethod
    def _sensitive_terms(evidence: list[Evidence]) -> tuple[str, ...]:
        terms: list[str] = []
        for item in evidence:
            for pattern, group in (
                (_PERSON_BEFORE_HISTORY, "person"),
                (_ORGANIZATION_AFTER_HISTORY, "organization"),
            ):
                for match in pattern.finditer(item.text):
                    terms.extend(match.group(group).split())
        return tuple(dict.fromkeys(terms))

    @staticmethod
    def _messages(
        user_request: str,
        evidence: list[Evidence],
        *,
        retry_code: str | None = None,
    ) -> list[Message]:
        retry = ""
        if retry_code:
            retry = (
                f"\n上一次结果未通过确定性校验：{retry_code}。"
                "重新生成，不要解释错误。"
                "只输出一个 JSON 对象，合法格式示例："
                f"\n{json.dumps(_RETRY_EXAMPLE, ensure_ascii=False)}"
            )
        evidence_text = "\n\n".join(
            f"Evidence {item.evidence_id}:\n{item.text[:2000]}"
            for item in evidence[:10]
        )
        return [
            Message(
                role="system",
                content=(
                    "你为公开岗位搜索生成最小化参数。只输出一个 JSON 对象，"
                    "字段必须为 query、location、evidence_refs、explicit_freshness。"
                    "explicit_freshness 只在用户明确要求最新、网上或当前招聘时为 true。"
                    "query 只能包含"
                    "岗位类别和技术关键词，不超过 6 个词；location 只来自用户"
                    "明确提出的地点，没有则为 null；evidence_refs 只引用下方"
                    " Evidence ID。不得输出姓名、公司履历、日期、联系方式、"
                    "简历句子、秘密或其他个人信息。"
                    f"{retry}"
                ),
            ),
            Message(
                role="user",
                content=(
                    f"用户请求：{user_request[:1000]}\n\n"
                    f"{evidence_text}"
                ),
            ),
        ]
