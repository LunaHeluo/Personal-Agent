from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from starter_agent.domain.models import ToolResult
from starter_agent.job_research.candidates import (
    assess_job_candidate,
    rank_job_candidates,
)
from starter_agent.job_research.query_planner import build_job_query_plan
from starter_agent.tools.base import Tool, ToolContext
from starter_agent.tools.adapters.serpapi_location import (
    SerpApiLocationResolver,
)


KeyResolver = Callable[[], tuple[str, str | None, str | None]]
SENSITIVE_QUERY_KEYS = {
    "api_key",
    "apikey",
    "key",
    "token",
    "access_token",
}
DETAIL_DISCOVERY_TERMS = (
    '"responsibilities" "requirements" current opening apply'
)


@dataclass(frozen=True, slots=True)
class _SearchArguments:
    query: str
    location: str
    location_alias: str | None
    limit: int
    query_variants: tuple[str, ...] = ()
    hl: str | None = None
    gl: str | None = None
    google_domain: str = "google.com"
    expand_location_aliases: bool = False
    location_aliases: tuple[str, ...] = ()
    plan_reason_codes: tuple[str, ...] = ()


class SerpApiRequestError(Exception):
    def __init__(self, failure_type: str, attempts: int):
        super().__init__(failure_type)
        self.failure_type = failure_type
        self.attempts = attempts


def sanitize_url(value: str) -> str:
    if not value:
        return ""
    value = value.replace("\\u003d", "=").replace("\\u0026", "&")
    split = urlsplit(value)
    if split.scheme not in {"http", "https"}:
        return ""
    query = [
        (key, item)
        for key, item in parse_qsl(split.query, keep_blank_values=True)
        if key.lower() not in SENSITIVE_QUERY_KEYS
    ]
    return urlunsplit(
        (split.scheme, split.netloc, split.path, urlencode(query), "")
    )


class SearchJobsSerpApiTool(Tool):
    name = "search_jobs_serpapi"
    description = (
        "Search public job listings with sources and retrieval timestamps. "
        "Use structured job keywords, location, and desired result count. "
        "Results are leads that must be verified on the source page."
    )
    risk_level = "read"
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Job keywords, such as AI Agent engineer jobs.",
                "minLength": 2,
                "maxLength": 300,
            },
            "location": {
                "type": "string",
                "description": "Optional city or region, such as Sydney.",
                "maxLength": 100,
            },
            "location_alias": {
                "type": "string",
                "description": "Optional Latin alias; validated by Locations API.",
                "minLength": 1,
                "maxLength": 100,
                "pattern": "^[A-Za-z][A-Za-z0-9 .,'()&/\\-]*$",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "default": 5,
            },
            "query_variants": {
                "type": "array",
                "items": {"type": "string", "minLength": 2, "maxLength": 300},
                "minItems": 1,
                "maxItems": 12,
            },
            "hl": {"type": "string", "pattern": "^[a-z]{2}(?:-[a-z]{2})?$"},
            "gl": {"type": "string", "pattern": "^[a-z]{2}$"},
            "google_domain": {"type": "string", "enum": ["google.com"]},
            "expand_location_aliases": {"type": "boolean", "default": False},
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        key_resolver: KeyResolver | None = None,
        *,
        client: Any | None = None,
        timeout: float = 15,
        max_retries: int = 1,
        retry_backoff_seconds: float = 0.5,
        location_resolver: Any | None = None,
    ) -> None:
        self.key_resolver = key_resolver or self._fallback_key_resolver
        self.client = client
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.location_resolver = location_resolver or SerpApiLocationResolver()

    @staticmethod
    def _fallback_key_resolver() -> tuple[str, str | None, str]:
        return "primary", os.getenv("SERPAPI_API_KEY"), "SERPAPI_API_KEY"

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        parsed = self._validate_arguments(arguments)
        if isinstance(parsed, ToolResult):
            return parsed
        query, location, limit = parsed.query, parsed.location, parsed.limit
        profile, api_key, api_key_env = self.key_resolver()
        safe_metadata = {
            "api_key_profile": profile,
            "api_key_env": api_key_env,
        }
        if not api_key or not api_key_env:
            return ToolResult(
                ok=False,
                display="当前 SerpAPI 凭据未配置",
                error_code="missing_api_key",
                metadata=safe_metadata,
            )

        requested_location = location
        location_resolution_status = "not_requested"
        if location:
            resolution = await self.location_resolver.resolve(location)
            if (
                not resolution.canonical_name
                and parsed.location_alias is not None
            ):
                alias_resolution = await self.location_resolver.resolve(
                    parsed.location_alias
                )
                if alias_resolution.canonical_name:
                    resolution = alias_resolution
            location_resolution_status = resolution.status
            if resolution.canonical_name:
                location = resolution.canonical_name
            else:
                query = " ".join((query, requested_location))
                parsed = replace(parsed, query=query)
                location = ""
            if parsed.expand_location_aliases:
                language = "zh-cn" if re.search(r"[\u3400-\u9fff]", f"{query} {requested_location}") else "en"
                plan = build_job_query_plan(
                    query=query,
                    requested_location=requested_location,
                    resolution=resolution,
                    user_language=language,
                )
                parsed = replace(
                    parsed,
                    query_variants=plan.queries,
                    hl=plan.hl,
                    gl=plan.gl,
                    location_aliases=plan.location_aliases,
                    plan_reason_codes=plan.reason_codes,
                )

        try:
            if self.client is not None:
                result = await self._dispatch_search(
                    self.client,
                    parsed,
                    location,
                    requested_location,
                    limit,
                    profile,
                    api_key,
                    api_key_env,
                )
            else:
                async with httpx.AsyncClient() as client:
                    result = await self._dispatch_search(
                        client,
                        parsed,
                        location,
                        requested_location,
                        limit,
                        profile,
                        api_key,
                        api_key_env,
                    )
            metadata = {
                **result.metadata,
                "location_resolution_status": location_resolution_status,
            }
            data = result.data
            if isinstance(data, dict):
                data = {
                    **data,
                    "location": requested_location,
                    "resolved_location": location or None,
                }
            return result.model_copy(update={"data": data, "metadata": metadata})
        except SerpApiRequestError as exc:
            metadata = {
                **safe_metadata,
                "failure_type": exc.failure_type,
                "attempts": exc.attempts,
            }
            if exc.failure_type == "timeout":
                return ToolResult(
                    ok=False,
                    display=(
                        "连接 SerpAPI 超时，已自动重试仍未成功；"
                        "请稍后再试"
                    ),
                    error_code="search_timeout",
                    retryable=True,
                    metadata=metadata,
                )
            if exc.failure_type == "connection_failed":
                return ToolResult(
                    ok=False,
                    display=(
                        "无法连接 SerpAPI，请检查当前网络、代理或 DNS 后重试"
                    ),
                    error_code="search_connection_failed",
                    retryable=True,
                    metadata=metadata,
                )
            return ToolResult(
                ok=False,
                display="SerpAPI 连接被中断，已自动重试；请稍后再试",
                error_code="search_transport_error",
                retryable=True,
                metadata=metadata,
            )
        except httpx.HTTPStatusError as exc:
            return self._http_error(
                exc.response.status_code,
                safe_metadata,
                response=exc.response,
            )
        except (ValueError, TypeError):
            return ToolResult(
                ok=False,
                display="SerpAPI 返回了无法解析的响应，请稍后重试",
                error_code="invalid_response",
                retryable=True,
                metadata={**safe_metadata, "failure_type": "invalid_response"},
            )
        except httpx.HTTPError:
            return ToolResult(
                ok=False,
                display="SerpAPI 网络请求失败，请检查网络后重试",
                error_code="search_transport_error",
                retryable=True,
                metadata={**safe_metadata, "failure_type": "http_error"},
            )

    async def _dispatch_search(
        self,
        client: Any,
        parsed: _SearchArguments,
        resolved_location: str,
        requested_location: str,
        limit: int,
        profile: str,
        api_key: str,
        api_key_env: str,
    ) -> ToolResult:
        if parsed.query_variants:
            return await self._search_variants(
                client,
                parsed.query_variants,
                resolved_location,
                limit,
                profile,
                api_key,
                api_key_env,
                hl=parsed.hl,
                gl=parsed.gl,
                google_domain=parsed.google_domain,
                location_aliases=parsed.location_aliases,
                plan_reason_codes=parsed.plan_reason_codes,
            )
        return await self._search_with_location_fallback(
            client,
            parsed.query,
            resolved_location,
            requested_location,
            limit,
            profile,
            api_key,
            api_key_env,
        )

    async def _search_variants(
        self,
        client: Any,
        queries: tuple[str, ...],
        location: str,
        limit: int,
        profile: str,
        api_key: str,
        api_key_env: str,
        *,
        hl: str | None,
        gl: str | None,
        google_domain: str,
        location_aliases: tuple[str, ...] = (),
        plan_reason_codes: tuple[str, ...] = (),
    ) -> ToolResult:
        retrieved_at = datetime.now(UTC).isoformat()
        semaphore = asyncio.Semaphore(4)

        async def run_one(query: str, engine: str) -> tuple[str, str, Any]:
            try:
                async with semaphore:
                    payload = await self._request(
                        client,
                        engine,
                        query,
                        location,
                        api_key,
                        hl=hl,
                        gl=gl,
                        google_domain=google_domain,
                    )
                error = self._payload_error(payload, profile, api_key_env)
                if error is not None and not self._is_no_results_error(payload):
                    return query, engine, error
                return query, engine, payload
            except SerpApiRequestError as exc:
                return query, engine, ToolResult(
                    ok=False,
                    error_code=f"search_{exc.failure_type}",
                    display="SerpAPI request failed",
                )
            except httpx.HTTPStatusError as exc:
                return query, engine, self._http_error(
                    exc.response.status_code,
                    {"api_key_profile": profile, "api_key_env": api_key_env},
                    response=exc.response,
                )
            except (ValueError, TypeError):
                return query, engine, ToolResult(
                    ok=False,
                    error_code="invalid_response",
                    display="SerpAPI response was invalid",
                )

        calls = [
            run_one(query, engine)
            for query in queries[:12]
            for engine in ("google_jobs", "google")
        ]
        outcomes = await asyncio.gather(*calls)
        raw_results: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        for query, engine, value in outcomes:
            if isinstance(value, ToolResult):
                failures.append(
                    {"query": query, "engine": engine, "error_code": value.error_code or "search_failed"}
                )
                continue
            parsed_rows = (
                self._parse_google_jobs(value, retrieved_at)
                if engine == "google_jobs"
                else self._parse_google(value, retrieved_at)
            )
            for row in parsed_rows:
                row["matched_queries"] = [query]
                row["search_engines"] = [engine]
                raw_results.append(row)

        merged: dict[str, dict[str, Any]] = {}
        for row in raw_results:
            url = str(row.get("url") or "")
            if not url:
                continue
            current = merged.get(url)
            if current is None:
                merged[url] = row
                continue
            current["matched_queries"] = list(dict.fromkeys([
                *current.get("matched_queries", []), *row.get("matched_queries", [])
            ]))
            current["search_engines"] = list(dict.fromkeys([
                *current.get("search_engines", []), *row.get("search_engines", [])
            ]))

        filtered_collection_count = sum(
            1
            for row in merged.values()
            if assess_job_candidate(
                row,
                url=str(row.get("url") or ""),
                title=str(row.get("title") or ""),
                location_aliases=location_aliases,
            ).page_kind
            == "collection_page"
        )
        diagnostic_ranked = rank_job_candidates(
            list(merged.values()),
            limit=10,
            location_aliases=location_aliases,
        )
        ranked = diagnostic_ranked[:limit]
        results = [item.model_dump(mode="json") for item in ranked]
        diagnostic_results = [
            item.model_dump(mode="json") for item in diagnostic_ranked
        ]
        safe_metadata = {
            "api_key_profile": profile,
            "api_key_env": api_key_env,
            "retrieved_at": retrieved_at,
            "result_count": len(results),
        }
        data = {
            "query": queries[0],
            "planned_queries": list(queries[:12]),
            "executed_queries": list(queries[:12]),
            "location": location,
            "search_engine": "google_jobs+google",
            "request_count": len(outcomes),
            "raw_result_count": len(raw_results),
            "deduplicated_count": len(merged),
            "filtered_collection_count": filtered_collection_count,
            "chinese_title_count": sum(
                1 for item in merged.values()
                if re.search(r"[\u3400-\u9fff]", str(item.get("title") or ""))
            ),
            "request_failures": failures,
            "location_aliases": list(location_aliases),
            "plan_reason_codes": list(plan_reason_codes),
            "results": results,
            "ranking_diagnostics": [
                {
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "score": item.get("score"),
                    "page_kind": item.get("page_kind"),
                    "reason_codes": item.get("reason_codes", []),
                    "matched_queries": item.get("matched_queries", []),
                    "search_engines": item.get("search_engines", []),
                }
                for item in diagnostic_results
            ],
        }
        if not results:
            return ToolResult(ok=False, data=data, error_code="no_results", display="No usable job results", metadata=safe_metadata)
        return ToolResult(ok=True, data=data, display=f"Found {len(results)} job leads", metadata=safe_metadata)

    async def _search(
        self,
        client: Any,
        query: str,
        location: str,
        limit: int,
        profile: str,
        api_key: str,
        api_key_env: str,
    ) -> ToolResult:
        retrieved_at = datetime.now(UTC).isoformat()
        jobs = await self._request(
            client, "google_jobs", query, location, api_key
        )
        provider_error = self._payload_error(jobs, profile, api_key_env)
        jobs_no_results = self._is_no_results_error(jobs)
        if provider_error and not jobs_no_results:
            return provider_error
        results = [] if jobs_no_results else self._parse_google_jobs(jobs, retrieved_at)
        ranked = rank_job_candidates(results, limit=limit)
        search_engine = "google_jobs"

        if not ranked:
            # Generic intent terms favor current detail/apply pages without
            # binding discovery to a city, employer, board, or ATS domain.
            fallback_query = " ".join(
                part
                for part in (query, location, DETAIL_DISCOVERY_TERMS)
                if part
            )
            generic = await self._request(
                client, "google", fallback_query, location, api_key
            )
            provider_error = self._payload_error(generic, profile, api_key_env)
            google_no_results = self._is_no_results_error(generic)
            if provider_error and not google_no_results:
                return provider_error
            results = (
                []
                if google_no_results
                else self._parse_google(generic, retrieved_at)
            )
            ranked = rank_job_candidates(results, limit=limit)
            search_engine = "google"
        elif len(ranked) < limit:
            discovery_query = " ".join(
                part
                for part in (query, location, DETAIL_DISCOVERY_TERMS)
                if part
            )
            generic = await self._request(
                client,
                "google",
                discovery_query,
                location,
                api_key,
            )
            generic_error = self._payload_error(
                generic,
                profile,
                api_key_env,
            )
            google_no_results = self._is_no_results_error(generic)
            if generic_error is None or google_no_results:
                organic = (
                    []
                    if google_no_results
                    else self._parse_google(generic, retrieved_at)
                )
                ranked = rank_job_candidates(
                    [
                        *(item.model_dump() for item in ranked),
                        *organic,
                    ],
                    limit=limit,
                )
                if organic:
                    search_engine = "google_jobs+google"

        safe_metadata = {
            "api_key_profile": profile,
            "api_key_env": api_key_env,
            "retrieved_at": retrieved_at,
        }
        results = [item.model_dump() for item in ranked]
        if not results:
            return ToolResult(
                ok=False,
                display="没有找到可用的岗位搜索结果",
                error_code="no_results",
                metadata=safe_metadata,
            )

        return ToolResult(
            ok=True,
            data={
                "query": query,
                "location": location,
                "api_key_profile": profile,
                "api_key_env": api_key_env,
                "search_engine": search_engine,
                "results": results,
            },
            display=(
                f"找到 {len(results)} 条岗位线索，"
                "请打开来源确认岗位是否仍有效"
            ),
            metadata={**safe_metadata, "result_count": len(results)},
        )

    async def _search_with_location_fallback(
        self,
        client: Any,
        query: str,
        location: str,
        requested_location: str,
        limit: int,
        profile: str,
        api_key: str,
        api_key_env: str,
    ) -> ToolResult:
        try:
            return await self._search(
                client,
                query,
                location,
                limit,
                profile,
                api_key,
                api_key_env,
            )
        except httpx.HTTPStatusError as exc:
            code, _summary = self._safe_provider_error(exc.response)
            if (
                exc.response.status_code != 400
                or not location
                or code != "unsupported_location"
            ):
                raise
        fallback_query = " ".join(
            part for part in (query, requested_location) if part
        )
        result = await self._search(
            client,
            fallback_query,
            "",
            limit,
            profile,
            api_key,
            api_key_env,
        )
        return result.model_copy(
            update={
                "metadata": {
                    **result.metadata,
                    "location_fallback_used": True,
                }
            }
        )

    async def _request(
        self,
        client: Any,
        engine: str,
        query: str,
        location: str,
        api_key: str,
        *,
        hl: str | None = None,
        gl: str | None = None,
        google_domain: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "engine": engine,
            "q": query,
            "api_key": api_key,
        }
        if location:
            params["location"] = location
        if hl:
            params["hl"] = (
                hl.split("-", 1)[0] if engine == "google_jobs" else hl
            )
        if gl:
            params["gl"] = gl
        if google_domain:
            params["google_domain"] = google_domain
        response = None
        for attempt in range(1, self.max_retries + 2):
            try:
                response = await client.get(
                    "https://serpapi.com/search.json",
                    params=params,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                break
            except httpx.HTTPStatusError as exc:
                if (
                    exc.response.status_code >= 500
                    and attempt <= self.max_retries
                ):
                    await asyncio.sleep(self.retry_backoff_seconds * attempt)
                    continue
                raise
            except httpx.TransportError as exc:
                if attempt <= self.max_retries:
                    await asyncio.sleep(self.retry_backoff_seconds * attempt)
                    continue
                if isinstance(exc, httpx.TimeoutException):
                    failure_type = "timeout"
                elif isinstance(exc, httpx.ConnectError):
                    failure_type = "connection_failed"
                else:
                    failure_type = "transport_interrupted"
                raise SerpApiRequestError(failure_type, attempt) from exc
        if response is None:
            raise SerpApiRequestError("transport_interrupted", 0)
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("SerpAPI response must be an object")
        return payload

    @staticmethod
    def _validate_arguments(
        arguments: dict[str, Any],
    ) -> _SearchArguments | ToolResult:
        query = arguments.get("query")
        location = arguments.get("location", "")
        location_alias = arguments.get("location_alias")
        limit = arguments.get("limit", 5)
        query_variants = arguments.get("query_variants", ())
        hl = arguments.get("hl")
        gl = arguments.get("gl")
        google_domain = arguments.get("google_domain", "google.com")
        expand_location_aliases = arguments.get("expand_location_aliases", False)
        if (
            not isinstance(query, str)
            or not 2 <= len(query.strip()) <= 300
            or not isinstance(location, str)
            or len(location) > 100
            or (
                location_alias is not None
                and (
                    not isinstance(location_alias, str)
                    or re.fullmatch(
                        r"[A-Za-z][A-Za-z0-9 .,'()&/\-]*",
                        location_alias,
                    )
                    is None
                    or not 1 <= len(location_alias.strip()) <= 100
                )
            )
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 10
            or not isinstance(query_variants, (list, tuple))
            or (
                "query_variants" in arguments
                and not 1 <= len(query_variants) <= 12
            )
            or any(
                not isinstance(item, str) or not 2 <= len(item.strip()) <= 300
                for item in query_variants
            )
            or (hl is not None and (not isinstance(hl, str) or re.fullmatch(r"[a-zA-Z]{2}(?:-[a-zA-Z]{2})?", hl) is None))
            or (gl is not None and (not isinstance(gl, str) or re.fullmatch(r"[a-zA-Z]{2}", gl) is None))
            or google_domain != "google.com"
            or not isinstance(expand_location_aliases, bool)
        ):
            return ToolResult(
                ok=False,
                display="岗位搜索参数不正确",
                error_code="invalid_arguments",
            )
        return _SearchArguments(
            query=query.strip(),
            location=location.strip(),
            location_alias=(
                location_alias.strip()
                if isinstance(location_alias, str)
                else None
            ),
            limit=limit,
            query_variants=tuple(item.strip() for item in query_variants),
            hl=hl.casefold() if isinstance(hl, str) else None,
            gl=gl.casefold() if isinstance(gl, str) else None,
            google_domain=google_domain,
            expand_location_aliases=expand_location_aliases,
        )

    @staticmethod
    def _payload_error(
        payload: dict[str, Any], profile: str, api_key_env: str
    ) -> ToolResult | None:
        error = payload.get("error")
        if not error:
            return None
        text = str(error).lower()
        metadata = {
            "api_key_profile": profile,
            "api_key_env": api_key_env,
            "failure_type": "provider_error",
        }
        if "invalid api key" in text or "not authorized" in text:
            return ToolResult(
                ok=False,
                display="SerpAPI Key 无效或无法使用",
                error_code="authentication_failed",
                metadata=metadata,
            )
        if "rate" in text:
            return ToolResult(
                ok=False,
                display="SerpAPI 请求过于频繁",
                error_code="rate_limited",
                retryable=True,
                metadata=metadata,
            )
        if "credit" in text or "quota" in text or "plan" in text:
            return ToolResult(
                ok=False,
                display="SerpAPI 搜索额度不足",
                error_code="quota_exceeded",
                metadata=metadata,
            )
        return ToolResult(
            ok=False,
            display="SerpAPI 返回了无法处理的错误",
            error_code="search_failed",
            retryable=True,
            metadata=metadata,
        )

    @staticmethod
    def _is_no_results_error(payload: dict[str, Any]) -> bool:
        error = str(payload.get("error") or "").lower()
        return "hasn't returned any results" in error or "no results" in error

    @staticmethod
    def _http_error(
        status: int,
        metadata: dict[str, Any],
        *,
        response: httpx.Response | None = None,
    ) -> ToolResult:
        if status in {401, 403}:
            return ToolResult(
                ok=False,
                display="SerpAPI Key 无效或无法使用",
                error_code="authentication_failed",
                metadata=metadata,
            )
        if status == 429:
            return ToolResult(
                ok=False,
                display="SerpAPI 请求过于频繁",
                error_code="rate_limited",
                retryable=True,
                metadata={**metadata, "failure_type": "rate_limited"},
            )
        if status in {500, 502, 503, 504}:
            return ToolResult(
                ok=False,
                display=f"SerpAPI 服务暂时异常（HTTP {status}），请稍后重试",
                error_code="service_unavailable",
                retryable=True,
                metadata={**metadata, "failure_type": f"http_{status}"},
            )
        if status == 400:
            provider_error_code, provider_error_summary = (
                SearchJobsSerpApiTool._safe_provider_error(response)
            )
            return ToolResult(
                ok=False,
                display="SerpAPI 无法处理当前搜索参数，请调整关键词或地点",
                error_code="invalid_search_request",
                metadata={
                    **metadata,
                    "failure_type": "http_400",
                    "provider_error_code": provider_error_code,
                    "provider_error_summary": provider_error_summary,
                },
            )
        return ToolResult(
            ok=False,
            display=f"岗位搜索服务返回异常（HTTP {status}）",
            error_code="search_failed",
            retryable=True,
            metadata={**metadata, "failure_type": f"http_{status}"},
        )

    @staticmethod
    def _safe_provider_error(
        response: httpx.Response | None,
    ) -> tuple[str, str]:
        summary = "SerpAPI rejected the search request"
        if response is not None:
            try:
                payload = response.json()
            except (ValueError, TypeError):
                payload = None
            if isinstance(payload, dict) and isinstance(payload.get("error"), str):
                candidate = " ".join(payload["error"].split())[:200]
                candidate = re.sub(
                    r"(?i)(api[_ -]?key|token|authorization|cookie|password)"
                    r"\s*[:=]\s*\S+",
                    r"\1=[REDACTED]",
                    candidate,
                )
                if candidate:
                    summary = candidate
        lowered = summary.casefold()
        code = (
            "unsupported_location"
            if "unsupported" in lowered and "location" in lowered
            else "invalid_request"
        )
        return code, summary

    @staticmethod
    def _parse_google_jobs(
        payload: dict[str, Any], retrieved_at: str
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        jobs = payload.get("jobs_results", [])
        if not isinstance(jobs, list):
            return results
        for position, item in enumerate(jobs):
            if not isinstance(item, dict) or not item.get("title"):
                continue
            apply_options = item.get("apply_options")
            common = {
                "title": str(item.get("title", "")),
                "company": str(item.get("company_name", "")),
                "location": str(item.get("location", "")),
                "snippet": str(item.get("description", ""))[:1000],
                "source": "serpapi_google_jobs",
                "retrieved_at": retrieved_at,
                "provider_position": position,
            }
            if isinstance(apply_options, list):
                for option in apply_options:
                    if not isinstance(option, dict):
                        continue
                    results.append(
                        {
                            **common,
                            "url": sanitize_url(str(option.get("link") or "")),
                            "url_kind": "structured_apply",
                        }
                    )
            if item.get("share_link"):
                results.append(
                    {
                        **common,
                        "url": sanitize_url(str(item.get("share_link") or "")),
                        "url_kind": "structured_share",
                    }
                )
        return results

    @staticmethod
    def _parse_google(
        payload: dict[str, Any], retrieved_at: str
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        items = payload.get("organic_results", [])
        if not isinstance(items, list):
            return results
        for position, item in enumerate(items):
            if (
                not isinstance(item, dict)
                or not item.get("title")
                or not item.get("link")
            ):
                continue
            results.append(
                {
                    "title": str(item.get("title", "")),
                    "company": "",
                    "location": "",
                    "url": sanitize_url(str(item.get("link", ""))),
                    "snippet": str(item.get("snippet", ""))[:1000],
                    "source": "serpapi_google",
                    "retrieved_at": retrieved_at,
                    "url_kind": "organic",
                    "provider_position": position,
                }
            )
        return results
