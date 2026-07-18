from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from starter_agent.domain.models import ToolResult
from starter_agent.tools.base import Tool, ToolContext


KeyResolver = Callable[[], tuple[str, str | None, str | None]]
SENSITIVE_QUERY_KEYS = {
    "api_key",
    "apikey",
    "key",
    "token",
    "access_token",
}


class SerpApiRequestError(Exception):
    def __init__(self, failure_type: str, attempts: int):
        super().__init__(failure_type)
        self.failure_type = failure_type
        self.attempts = attempts


def sanitize_url(value: str) -> str:
    if not value:
        return ""
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
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "default": 5,
            },
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
    ) -> None:
        self.key_resolver = key_resolver or self._fallback_key_resolver
        self.client = client
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds

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
        query, location, limit = parsed
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

        try:
            if self.client is not None:
                return await self._search(
                    self.client,
                    query,
                    location,
                    limit,
                    profile,
                    api_key,
                    api_key_env,
                )
            async with httpx.AsyncClient() as client:
                return await self._search(
                    client,
                    query,
                    location,
                    limit,
                    profile,
                    api_key,
                    api_key_env,
                )
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
            return self._http_error(exc.response.status_code, safe_metadata)
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
        search_engine = "google_jobs"

        if not results:
            fallback_query = " ".join(
                part for part in (query, location, "jobs") if part
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
            search_engine = "google"

        safe_metadata = {
            "api_key_profile": profile,
            "api_key_env": api_key_env,
            "retrieved_at": retrieved_at,
        }
        results = results[:limit]
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

    async def _request(
        self,
        client: Any,
        engine: str,
        query: str,
        location: str,
        api_key: str,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "engine": engine,
            "q": query,
            "api_key": api_key,
        }
        if location:
            params["location"] = location
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
    ) -> tuple[str, str, int] | ToolResult:
        query = arguments.get("query")
        location = arguments.get("location", "")
        limit = arguments.get("limit", 5)
        if (
            not isinstance(query, str)
            or not 2 <= len(query.strip()) <= 300
            or not isinstance(location, str)
            or len(location) > 100
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 10
        ):
            return ToolResult(
                ok=False,
                display="岗位搜索参数不正确",
                error_code="invalid_arguments",
            )
        return query.strip(), location.strip(), limit

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
    def _http_error(status: int, metadata: dict[str, Any]) -> ToolResult:
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
            return ToolResult(
                ok=False,
                display="SerpAPI 无法处理当前搜索参数，请调整关键词或地点",
                error_code="invalid_search_request",
                metadata={**metadata, "failure_type": "http_400"},
            )
        return ToolResult(
            ok=False,
            display=f"岗位搜索服务返回异常（HTTP {status}）",
            error_code="search_failed",
            retryable=True,
            metadata={**metadata, "failure_type": f"http_{status}"},
        )

    @staticmethod
    def _parse_google_jobs(
        payload: dict[str, Any], retrieved_at: str
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        jobs = payload.get("jobs_results", [])
        if not isinstance(jobs, list):
            return results
        for item in jobs:
            if not isinstance(item, dict) or not item.get("title"):
                continue
            url = str(item.get("share_link") or "")
            apply_options = item.get("apply_options")
            if not url and isinstance(apply_options, list) and apply_options:
                first = apply_options[0]
                if isinstance(first, dict):
                    url = str(first.get("link") or "")
            results.append(
                {
                    "title": str(item.get("title", "")),
                    "company": str(item.get("company_name", "")),
                    "location": str(item.get("location", "")),
                    "url": sanitize_url(url),
                    "snippet": str(item.get("description", ""))[:1000],
                    "source": "serpapi_google_jobs",
                    "retrieved_at": retrieved_at,
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
        for item in items:
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
                }
            )
        return results
