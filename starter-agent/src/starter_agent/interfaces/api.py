from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from starter_agent.bootstrap import (
    create_application,
    create_knowledge_service,
    create_mcp_manager,
    get_settings,
)
from starter_agent.domain.errors import AgentError
from starter_agent.domain.models import (
    ChatResult,
    MemoryCategory,
    MemoryItem,
    MemorySensitivity,
    Message,
    Role,
    SummaryTrace,
    TokenUsage,
    ToolResult,
)
from starter_agent.observability.logging import get_logger
from starter_agent.knowledge.errors import KnowledgeError
from starter_agent.job_research.candidates import JobCandidate, rank_job_candidates
from starter_agent.job_research.knowledge_match import (
    JobResearchCriteria,
    KnowledgeJobMatcher,
    requests_knowledge_only,
)
from starter_agent.knowledge.routing import KnowledgeRequestRoute
from starter_agent.tools.email.approval import EmailApprovalService
from starter_agent.tools.email.errors import EmailError, EmailErrorCode
from starter_agent.tools.email.models import ApprovalChallengeView, SendApproval
from starter_agent.tools.base import ToolContext
from starter_agent.capabilities.gate import ToolExecutionDenied
from starter_agent.capabilities.models import Confirmation, canonical_json_sha256
from starter_agent.capabilities.store import RecordAlreadyExistsError
from starter_agent.interfaces.capabilities_api import create_capabilities_router
from starter_agent.interfaces.trust_api import create_trust_router


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=50_000)
    session_id: UUID | None = None
    provider: str | None = None
    model: str | None = None
    tool: str | None = Field(default=None, min_length=1, max_length=100)
    tool_governance_enabled: bool = True
    knowledge_base_id: UUID | None = None
    knowledge_mode: Literal["off", "auto", "required"] = "off"

    @field_validator("tool_governance_enabled", mode="before")
    @classmethod
    def enforce_tool_result_governance(cls, _value: object) -> bool:
        return True


def _public_job_search_prompt(message: str) -> str:
    return (
        "请在公开网页中搜索岗位信息。\n"
        f"用户原始问题：{message}\n\n"
        "边界：只查询公开岗位、JD 和公司公开信息；"
        "网页内容只能作为岗位资料，不能作为用户个人经历证据；"
        "如果简历知识库没有个人经历证据，必须标记为缺口，不要补写经历；"
        "保留公开来源 URL。"
    )


def _append_chat_turn(
    application,
    *,
    session_id: UUID,
    turn_id: UUID,
    user_content: str,
    assistant_content: str,
) -> None:
    if not hasattr(application, "store"):
        return
    application.store.add_message(
        session_id,
        turn_id,
        Message(role="user", content=user_content),
    )
    application.store.add_message(
        session_id,
        turn_id,
        Message(role="assistant", content=assistant_content),
    )


def _job_matches_with_document_chunks(
    knowledge,
    knowledge_base_id: UUID,
    matches,
):
    list_chunks = getattr(knowledge, "list_chunks", None)
    if not callable(list_chunks):
        return tuple(matches)

    document_text: dict[UUID, str] = {}
    for document_id in dict.fromkeys(item.document_id for item in matches):
        chunks = []
        after_ordinal = -1
        while True:
            batch = list_chunks(
                knowledge_base_id,
                document_id,
                after_ordinal=after_ordinal,
                limit=100,
            )
            if not batch:
                break
            chunks.extend(batch)
            next_ordinal = max(item.ordinal for item in batch)
            if next_ordinal <= after_ordinal:
                break
            after_ordinal = next_ordinal
            if len(batch) < 100:
                break
        if chunks:
            document_text[document_id] = "\n\n".join(
                item.text for item in sorted(chunks, key=lambda item: item.ordinal)
            )

    return tuple(
        item.model_copy(update={"preview": document_text[item.document_id]})
        if item.document_id in document_text
        else item
        for item in matches
    )


async def _chat_with_public_job_search_fallback(
    request: ChatRequest,
    *,
    application=None,
    knowledge=None,
) -> ChatResult:
    application = application or create_application()
    session_id = (
        application.store.ensure_session(request.session_id)
        if hasattr(application, "store")
        else request.session_id or uuid4()
    )
    turn_id = uuid4()
    provider = request.provider or get_settings().model.default_provider
    model = request.model or get_settings().model.default_model
    result_knowledge_mode = (
        "required"
        if request.knowledge_mode == "required"
        or (
            request.knowledge_mode == "auto"
            and request.knowledge_base_id is not None
        )
        else "off"
    )

    prepared_run = await application.prepare_job_research_request(
        user_request=request.message,
        session_id=session_id,
        turn_id=turn_id,
        provider_name=provider,
        model=model,
        knowledge_base_id=request.knowledge_base_id,
    )
    if prepared_run.status != "search_profile_ready":
        content = _public_job_search_failure_answer(prepared_run)
        _append_chat_turn(
            application,
            session_id=session_id,
            turn_id=turn_id,
            user_content=request.message,
            assistant_content=content,
        )
        return ChatResult(
            session_id=session_id,
            turn_id=turn_id,
            content=content,
            provider=provider,
            model=model,
            tool_calls=len(prepared_run.trace),
            knowledge_mode=result_knowledge_mode,
        )
    profile = prepared_run.data.get("search_profile", {})
    if knowledge is not None and isinstance(profile, dict):
        profile_query = str(profile.get("query") or request.message)
        job_matches = knowledge.retrieve(
            request.knowledge_base_id,
            profile_query,
            top_k=6,
            document_types=["job_description"],
        )
        if (
            not job_matches
            and request.message.strip() != profile_query.strip()
            and not bool(profile.get("explicit_freshness"))
        ):
            job_matches = knowledge.retrieve(
                request.knowledge_base_id,
                request.message,
                top_k=6,
                document_types=["job_description"],
            )
        job_matches = _job_matches_with_document_chunks(
            knowledge,
            request.knowledge_base_id,
            job_matches,
        )
        decision = KnowledgeJobMatcher().evaluate(
            criteria=JobResearchCriteria(
                location=(
                    profile.get("location")
                    if isinstance(profile.get("location"), str)
                    else None
                ),
                role_terms=tuple(
                    item
                    for item in profile.get("role_terms", [])
                    if isinstance(item, str)
                ),
                explicit_freshness=bool(profile.get("explicit_freshness")),
            ),
            matches=tuple(job_matches),
            now=datetime.now(UTC),
            freshness_days=get_settings().job_research.jd_freshness_days,
        )
        if decision.use_knowledge:
            try:
                rag = await knowledge.answer(
                    request.knowledge_base_id,
                    request.message,
                    provider_name=request.provider,
                    model=request.model,
                )
            except KnowledgeError as exc:
                if exc.code not in {
                    "generation_invalid_output",
                    "citation_validation_failed",
                }:
                    raise
                get_logger(
                    error_code=exc.code,
                    matched_job_count=len(decision.matches),
                    session_id=str(session_id),
                    turn_id=str(turn_id),
                ).warning("knowledge.job_answer_deterministic_fallback")
                return _saved_job_matches_chat_result(
                    request,
                    application=application,
                    session_id=session_id,
                    turn_id=turn_id,
                    matches=decision.matches,
                    tool_calls=len(prepared_run.trace),
                )
            return _rag_chat_result(
                request,
                rag,
                application,
                session_id=session_id,
                turn_id=turn_id,
                matched_jobs=decision.matches,
            )
        if requests_knowledge_only(request.message):
            content = (
                "知识库中没有符合当前岗位条件且仍可使用的 JD；"
                "已按你的要求跳过联网搜索。"
                f"\n原因：{decision.reason_code}"
            )
            _append_chat_turn(
                application,
                session_id=session_id,
                turn_id=turn_id,
                user_content=request.message,
                assistant_content=content,
            )
            return ChatResult(
                session_id=session_id,
                turn_id=turn_id,
                content=content,
                provider=provider,
                model=model,
                tool_calls=len(prepared_run.trace),
                knowledge_mode=result_knowledge_mode,
                refusal_reason=decision.reason_code,
            )

    if requests_knowledge_only(request.message):
        content = "未选择可用的知识库，已按你的要求跳过联网搜索。\n原因：missing_jd"
        _append_chat_turn(
            application,
            session_id=session_id,
            turn_id=turn_id,
            user_content=request.message,
            assistant_content=content,
        )
        return ChatResult(
            session_id=session_id,
            turn_id=turn_id,
            content=content,
            provider=provider,
            model=model,
            tool_calls=len(prepared_run.trace),
            knowledge_mode=result_knowledge_mode,
            refusal_reason="missing_jd",
        )

    search_run = await application.search_prepared_job_research(
        prepared=prepared_run,
        session_id=session_id,
        turn_id=turn_id,
        limit=get_settings().job_research.max_candidate_urls,
        knowledge_base_id=request.knowledge_base_id,
    )
    tool_calls = len(search_run.trace)
    if search_run.status != "waiting_for_url_selection":
        content = _public_job_search_failure_answer(search_run)
        _append_chat_turn(
            application,
            session_id=session_id,
            turn_id=turn_id,
            user_content=request.message,
            assistant_content=content,
        )
        return ChatResult(
            session_id=session_id,
            turn_id=turn_id,
            content=content,
            provider=provider,
            model=model,
            tool_calls=tool_calls,
            knowledge_mode=result_knowledge_mode,
        )
    search_result = ToolResult(
        ok=True,
        data={"results": search_run.data.get("results", [])},
        display="岗位搜索完成。",
    )
    jd_result: ToolResult | None = None
    candidates = _public_job_candidates(search_result)
    if candidates:
        analysis_run = await application.analyze_job_research_candidates(
            query=request.message,
            candidates=candidates,
            session_id=session_id,
            turn_id=turn_id,
            target_count=get_settings().job_research.target_valid_jds,
            knowledge_base_id=request.knowledge_base_id,
            resume_evidence=search_run.data.get("resume_evidence", []),
        )
        tool_calls += len(analysis_run.trace)
        verified_jobs = analysis_run.data.get("jobs", [])
        partial_jobs = analysis_run.data.get("partial_jobs", [])
        job_results = analysis_run.data.get("job_results", [])
        analyses_by_url = {
            item["job"].get("source_url"): item.get("analysis", [])
            for item in job_results
            if isinstance(item, dict)
            and isinstance(item.get("job"), dict)
            and isinstance(item["job"].get("source_url"), str)
        }
        jobs = [
            {
                "ok": True,
                **job,
                "analysis": analyses_by_url.get(job.get("source_url"), []),
            }
            for job in verified_jobs
            if isinstance(job, dict)
        ]
        attempts = analysis_run.data.get("candidate_attempts", [])
        success_count = len(jobs)
        partial_count = len(partial_jobs)
        dependency_unavailable = analysis_run.status == "dependency_unavailable"
        if dependency_unavailable:
            missing = ", ".join(analysis_run.missing_dependencies)
            display = "Playwright MCP 当前不可用，尚未发起任何 JD 页面读取。"
            if missing:
                display += f" 缺失能力：{missing}"
        else:
            display = (
                f"通过 Playwright MCP 读取了 {success_count}/{len(candidates)} 个公开 JD。"
            )
        jd_result = ToolResult(
            ok=(success_count + partial_count) > 0,
            data={
                "jobs": jobs,
                "partial_jobs": [
                    item for item in partial_jobs if isinstance(item, dict)
                ],
                "candidate_attempts": attempts,
                "requested_urls": [item.url for item in candidates],
                "success_count": success_count,
                "failure_count": sum(
                    1
                    for item in attempts
                    if isinstance(item, dict) and item.get("status") not in {
                        "succeeded", "fallback_succeeded", "partial_verified"
                    }
                ),
            },
            display=display,
            error_code=(
                None
                if (success_count + partial_count) > 0
                else analysis_run.error_code or "job_description_unverified"
            ),
        )

    content = _public_job_search_answer(
        search_result=search_result,
        jd_result=jd_result,
    )
    _append_chat_turn(
        application,
        session_id=session_id,
        turn_id=turn_id,
        user_content=request.message,
        assistant_content=content,
    )
    return ChatResult(
        session_id=session_id,
        turn_id=turn_id,
        content=content,
        provider=provider,
        model=model,
        tool_calls=tool_calls,
        knowledge_mode=result_knowledge_mode,
    )


async def _classify_chat_request(request: ChatRequest, application):
    if (
        request.knowledge_mode == "required"
        and request.knowledge_base_id is None
    ):
        raise KnowledgeError("knowledge_base_not_found")
    return await application.route_knowledge_request(
        content=request.message,
        provider_name=request.provider,
        model=request.model,
    )


async def _dispatch_classified_chat(
    request: ChatRequest,
    *,
    application,
    route,
) -> ChatResult:
    if route.route is KnowledgeRequestRoute.CONVERSATION:
        return await application.chat(
            content=request.message,
            session_id=request.session_id,
            provider_name=request.provider,
            model=request.model,
            allow_tools=False,
        )

    if route.route is KnowledgeRequestRoute.JOB_RESEARCH:
        knowledge = (
            create_knowledge_service()
            if request.knowledge_base_id is not None
            and request.knowledge_mode != "off"
            else None
        )
        return await _chat_with_public_job_search_fallback(
            request,
            application=application,
            knowledge=knowledge,
        )

    use_knowledge = request.knowledge_mode == "required" or (
        request.knowledge_mode == "auto"
        and request.knowledge_base_id is not None
    )
    if use_knowledge:
        knowledge = create_knowledge_service()
        rag = await knowledge.answer(
            request.knowledge_base_id,
            request.message,
            provider_name=request.provider,
            model=request.model,
        )
        return _rag_chat_result(request, rag, application)

    return await application.chat(
        content=request.message,
        session_id=request.session_id,
        provider_name=request.provider,
        model=request.model,
        required_tool_name=request.tool,
        tool_governance_enabled=request.tool_governance_enabled,
    )


def _saved_job_matches_chat_result(
    request: ChatRequest,
    *,
    application,
    session_id: UUID,
    turn_id: UUID,
    matches,
    tool_calls: int,
) -> ChatResult:
    lines = [
        f"知识库中找到 {len(matches)} 个符合条件且仍可使用的 JD。",
        "以下内容直接来自已保存的 JD 证据，未补写岗位信息：",
    ]
    citations: list[dict[str, object]] = []
    for index, match in enumerate(matches, start=1):
        preview = match.preview.strip()
        title = next(
            (line.strip().lstrip("# ") for line in preview.splitlines() if line.strip()),
            match.filename,
        )
        excerpt = preview[:2400]
        if len(preview) > len(excerpt):
            excerpt += "\n[JD 内容已截断，可在知识库中查看完整文档]"
        lines.append(
            f"\n{index}. {title}\n{excerpt}\n来源：{match.source_ref}"
        )
        quote = preview[:240] or title
        citations.append(
            {
                "citation_id": f"saved-job-{index}",
                "document_id": str(match.document_id),
                "filename": match.filename,
                "document_version": match.version,
                "chunk_id": str(match.chunk_id),
                "page": match.page,
                "section": (
                    " / ".join(match.section_path)
                    if match.section_path
                    else None
                ),
                "start_line": match.start_line,
                "end_line": match.end_line,
                "quote": quote,
            }
        )
    content = "\n".join(lines)
    _append_chat_turn(
        application,
        session_id=session_id,
        turn_id=turn_id,
        user_content=request.message,
        assistant_content=content,
    )
    return ChatResult(
        session_id=session_id,
        turn_id=turn_id,
        content=content,
        provider=request.provider or get_settings().model.default_provider,
        model=request.model or get_settings().model.default_model,
        tool_calls=tool_calls,
        knowledge_mode="required",
        citations=citations,
    )


def _rag_chat_result(
    request: ChatRequest,
    rag,
    application,
    *,
    session_id: UUID | None = None,
    turn_id: UUID | None = None,
    matched_jobs=(),
) -> ChatResult:
    session_id = session_id or (
        application.store.ensure_session(request.session_id)
        if hasattr(application, "store")
        else request.session_id or uuid4()
    )
    turn_id = turn_id or uuid4()
    content = rag.answer
    if matched_jobs:
        matched_lines = [
            f"知识库中匹配到 {len(matched_jobs)} 个符合条件且仍可使用的 JD："
        ]
        for index, match in enumerate(matched_jobs, start=1):
            preview = match.preview.strip()
            title = next(
                (
                    line.strip().lstrip("# ")
                    for line in preview.splitlines()
                    if line.strip()
                ),
                match.filename,
            )
            matched_lines.append(
                f"{index}. {title}\n   来源：{match.source_ref}"
            )
        content = "\n".join([*matched_lines, "", rag.answer])
    if hasattr(application, "store"):
        application.store.add_message(
            session_id,
            turn_id,
            Message(role="user", content=request.message),
        )
        application.store.add_message(
            session_id,
            turn_id,
            Message(
                role="assistant",
                content=content,
                metadata={
                    "knowledge_mode": "required",
                    "citations": [
                        item.model_dump(mode="json")
                        for item in rag.citations
                    ],
                },
            ),
        )
    return ChatResult(
        session_id=session_id,
        turn_id=turn_id,
        content=content,
        provider=request.provider or get_settings().model.default_provider,
        model=request.model or get_settings().model.default_model,
        knowledge_mode="required",
        claims=[item.model_dump(mode="json") for item in rag.claims],
        citations=[item.model_dump(mode="json") for item in rag.citations],
        refusal_reason=rag.refusal_reason,
    )


def _public_job_search_failure_answer(search_run) -> str:
    if search_run.status == "search_profile_required":
        if search_run.error_code in {
            "invalid_json",
            "schema_validation_failed",
        }:
            return (
                "模型未能生成符合结构的岗位搜索条件，自动重试后仍未通过校验。"
                "这不代表简历缺失；可以重试，或补充目标岗位方向后继续。"
                f"\n错误码：{search_run.error_code}"
            )
        return (
            "当前简历证据不足以生成安全的公开岗位搜索条件。"
            "请补充目标岗位方向或技术关键词后重试。"
            f"\n错误码：{search_run.error_code or 'search_profile_required'}"
        )
    search = search_run.data.get("search") if isinstance(search_run.data, dict) else None
    if isinstance(search, dict) and isinstance(search.get("display"), str):
        display = search["display"]
    else:
        display = "公开岗位搜索失败，请稍后重试或调整搜索条件。"
    return f"{display}\n错误码：{search_run.error_code or 'search_failed'}"


def _public_job_urls(result: ToolResult) -> list[str]:
    if not result.ok or not isinstance(result.data, dict):
        return []
    rows = result.data.get("results")
    if not isinstance(rows, list):
        return []
    urls: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = row.get("url")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            urls.append(url)
        if len(urls) >= 3:
            break
    return urls


def _public_job_candidates(result: ToolResult) -> tuple[JobCandidate, ...]:
    if not result.ok or not isinstance(result.data, dict):
        return ()
    rows = result.data.get("results")
    if not isinstance(rows, list):
        return ()
    normalized = []
    for position, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        normalized.append(
            {
                **row,
                "url_kind": row.get("url_kind") or "organic",
                "provider_position": row.get("provider_position", position),
            }
        )
    return rank_job_candidates(
        normalized,
        limit=get_settings().job_research.max_candidate_urls,
    )


def _public_job_search_answer(
    *,
    search_result: ToolResult,
    jd_result: ToolResult | None,
) -> str:
    lines = ["已查询公开岗位。"]
    if not search_result.ok:
        lines.append(search_result.display or "岗位搜索失败。")
        if search_result.error_code:
            lines.append(f"错误码：{search_result.error_code}")
        return "\n".join(lines)

    rows = []
    if isinstance(search_result.data, dict):
        raw_rows = search_result.data.get("results")
        if isinstance(raw_rows, list):
            rows = [row for row in raw_rows if isinstance(row, dict)]
    if rows:
        lines.append("搜索结果：")
        for index, row in enumerate(rows[:3], start=1):
            title = row.get("title") or row.get("job_title") or "未命名岗位"
            company = row.get("company") or row.get("company_name") or "未知公司"
            url = row.get("url") or row.get("source_url") or "无 URL"
            lines.append(f"{index}. {title} · {company} · {url}")

    if jd_result is None:
        lines.append("搜索结果中没有可自动读取的公开 JD URL。")
        return "\n".join(lines)
    if not jd_result.ok:
        data = jd_result.data if isinstance(jd_result.data, dict) else {}
        partial_jobs = data.get("partial_jobs")
        if isinstance(partial_jobs, list) and partial_jobs:
            lines.append(
                "\u4ec5\u8bfb\u53d6\u5230\u90e8\u5206\u9a8c\u8bc1\u7684\u5c97\u4f4d\u9875\u9762\uff0c"
                "\u4e0d\u4f5c\u4e3a\u5b8c\u6574 JD \u63a8\u8350\uff1a"
            )
            for index, job in enumerate(partial_jobs[:3], start=1):
                if not isinstance(job, dict):
                    continue
                title = job.get("title") or "Unverified job"
                company = job.get("company") or "\u5f85\u786e\u8ba4"
                location = job.get("location") or "\u5f85\u786e\u8ba4"
                source_url = job.get("source_url") or ""
                reasons = job.get("validation_reason_codes")
                reason_text = (
                    ", ".join(str(item) for item in reasons)
                    if isinstance(reasons, list)
                    else "incomplete_job_description"
                )
                lines.append(f"{index}. {title} \u00b7 {company} \u00b7 {location}")
                lines.append(f"   \u5f85\u786e\u8ba4\uff1a{reason_text}")
                lines.append(f"   \u6765\u6e90\uff1a{source_url}")
        lines.append(jd_result.display or "自动读取 JD 失败。")
        if jd_result.error_code:
            lines.append(f"JD 错误码：{jd_result.error_code}")
        return "\n".join(lines)

    lines.append("已自动读取公开 JD 预览：")
    data = jd_result.data
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if isinstance(jobs, list):
        for index, job in enumerate(jobs[:3], start=1):
            if not isinstance(job, dict):
                continue
            title = job.get("title") or job.get("job_title") or "未命名 JD"
            company = job.get("company") or "未知公司"
            location = job.get("location") or "未知地点"
            source_url = job.get("source_url") or job.get("final_url") or ""
            lines.append(f"{index}. {title} · {company} · {location}")
            responsibilities = job.get("responsibilities")
            if isinstance(responsibilities, list) and responsibilities:
                lines.append(
                    "   岗位职责："
                    + "；".join(str(item) for item in responsibilities[:3])
                )
            requirements = job.get("requirements")
            if isinstance(requirements, list) and requirements:
                lines.append(
                    "   岗位要求："
                    + "；".join(str(item) for item in requirements[:5])
                )
            analysis = job.get("analysis")
            if isinstance(analysis, list) and analysis:
                matched = sum(
                    1
                    for item in analysis
                    if isinstance(item, dict) and item.get("status") == "matched"
                )
                gaps = sum(
                    1
                    for item in analysis
                    if isinstance(item, dict) and item.get("status") == "gap"
                )
                lines.append(f"   简历匹配：{matched} 项；证据缺口：{gaps} 项")
            lines.append(f"   来源：{source_url}")
    else:
        lines.append(jd_result.display or "JD 已读取。")
    lines.append("请选择一个岗位后，我再继续做最终匹配分析或确认入库。")
    partial_jobs = data.get("partial_jobs") if isinstance(data, dict) else None
    if isinstance(partial_jobs, list) and partial_jobs:
        lines.append(f"部分岗位证据（{len(partial_jobs)} 个，非完整 JD）：")
        for job in partial_jobs[:3]:
            if isinstance(job, dict):
                lines.append(
                    f"- {job.get('title') or '未命名岗位'} · "
                    f"{job.get('retrieval_method') or 'search_snippet'} · "
                    f"{job.get('source_url') or ''}"
                )
    attempts = data.get("candidate_attempts") if isinstance(data, dict) else None
    failed = [
        item for item in attempts or []
        if isinstance(item, dict)
        and item.get("status") not in {
            "succeeded", "fallback_succeeded", "partial_verified"
        }
    ]
    if failed:
        lines.append(f"抓取失败链接（{len(failed)} 个）：")
        for item in failed:
            code = (
                item.get("final_error_code")
                or item.get("error_code")
                or "mcp_unknown_error"
            )
            lines.append(f"- {item.get('source_url') or ''} · {code}")
    return "\n".join(lines)


class ProviderInfo(BaseModel):
    name: str
    type: str
    models: list[str] = Field(default_factory=list)
    is_default: bool = False
    has_api_key: bool = True


class ProvidersResponse(BaseModel):
    default_provider: str
    default_model: str
    providers: list[ProviderInfo]


class ToolInfo(BaseModel):
    name: str
    description: str | None = None
    risk_level: str | None = None
    source: str = "builtin"
    server: str = "builtin"
    type: str = "builtin"
    enabled: bool = True
    review: str = "approved"
    callable: bool = True


class ToolsResponse(BaseModel):
    tools: list[ToolInfo]


class SessionSummary(BaseModel):
    id: UUID
    created_at: datetime
    updated_at: datetime
    title: str | None = None
    message_count: int = 0
    last_message: str | None = None


class SessionListResponse(BaseModel):
    sessions: list[SessionSummary]
    total: int = 0
    offset: int = 0
    limit: int = 50
    has_more: bool = False


class HistoryMessage(BaseModel):
    role: Role
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    created_at: datetime
    turn_id: UUID


class SessionMessagesResponse(BaseModel):
    session_id: UUID
    messages: list[HistoryMessage]
    session_usage: TokenUsage = Field(default_factory=TokenUsage)
    max_total_tokens: int = 128_000
    token_budget_status: str = "normal"
    latest_summary_trace: SummaryTrace | None = None


class MemoryCreateRequest(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=2_000)
    category: MemoryCategory
    source_type: Literal[
        "user_confirmed", "local_file", "external_web", "email", "tool_output"
    ] = "user_confirmed"
    expires_at: datetime | None = None
    sensitivity: MemorySensitivity = "personal"
    confirmed: bool


class MemoryUpdateRequest(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=2_000)
    category: MemoryCategory
    expires_at: datetime | None = None
    sensitivity: MemorySensitivity = "personal"
    status: Literal["active", "disabled"] = "active"
    confirmed: bool


class MemoryListResponse(BaseModel):
    memories: list[MemoryItem]


class EmailApprovalChallengeRequest(BaseModel):
    session_id: UUID
    profile: str | None = Field(default=None, max_length=80)
    user_ref: str | None = Field(default=None, max_length=200)


class EmailApprovalActionRequest(BaseModel):
    session_id: UUID
    confirmed: bool = False


class EmailApprovalSendRequest(BaseModel):
    session_id: UUID
    idempotency_key: str = Field(min_length=16, max_length=200)


class KnowledgeRetrieveRequest(BaseModel):
    question: str = Field(min_length=1, max_length=10_000)
    top_k: int = Field(default=6, ge=1, le=50)
    document_ids: list[UUID] | None = None
    document_types: list[str] | None = None
    filenames: list[str] | None = None
    versions: list[int] | None = None


class KnowledgeAnswerRequest(BaseModel):
    question: str = Field(min_length=1, max_length=10_000)
    provider: str | None = None
    model: str | None = None


def _email_approval_service() -> EmailApprovalService:
    manager = create_application().runtime.tools.email_manager
    if manager is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "email_tools_not_enabled",
                "message": "邮件工具套装尚未启用",
            },
        )
    return EmailApprovalService(manager)


def _email_turn_id(approval_id: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"starter-agent:email-approval:{approval_id}")


def _email_gate_request(application, approval, draft):
    return application.runtime.gate.request_for_tool(
        caller="trusted-email-api",
        principal=approval.user_ref or "local-user",
        session_id=approval.session_id,
        turn_id=str(_email_turn_id(approval.approval_id)),
        call_id=f"api-email-send-{approval.approval_id}",
        tool_name="email_send",
        arguments={
            "profile": approval.profile,
            "draft_id": draft.draft_id,
            "expected_content_sha256": draft.content_sha256,
            "approval_id": approval.approval_id,
        },
    )


def _email_http_error(error: EmailError) -> HTTPException:
    return HTTPException(status_code=400, detail=error.public_payload())


def _knowledge_http_error(error: KnowledgeError) -> HTTPException:
    return HTTPException(
        status_code=error.http_status,
        detail=error.to_public_dict(),
    )


MEMORY_TTL_DAYS: dict[str, int] = {
    "profile": 365,
    "preference": 180,
    "constraint": 180,
    "verified_skill": 365,
    "application_state": 365,
}


def _memory_expiry(category: str, expires_at: datetime | None) -> datetime:
    if expires_at is not None:
        return expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=UTC)
    return datetime.now(UTC) + timedelta(days=MEMORY_TTL_DAYS[category])


def _validate_memory_write(
    *, source_type: str, confirmed: bool, expires_at: datetime | None
) -> None:
    if not confirmed:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "memory_confirmation_required",
                "message": "写入或修改长期记忆前需要用户明确确认",
            },
        )
    if source_type != "user_confirmed":
        raise HTTPException(
            status_code=400,
            detail={
                "code": "external_memory_source_not_allowed",
                "message": "网页、邮件、工具结果或未经核验的文件内容不能直接写入长期记忆",
            },
        )
    if expires_at is not None:
        normalized = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=UTC)
        if normalized <= datetime.now(UTC):
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "memory_expiry_invalid",
                    "message": "长期记忆的过期时间必须晚于当前时间",
                },
            )


def _summary_text(value: str | None, limit: int = 80) -> str | None:
    if not value:
        return None
    text = " ".join(value.split())
    return text if len(text) <= limit else f"{text[:limit - 1]}…"


@asynccontextmanager
async def _api_lifespan(_api: FastAPI):
    manager = None
    try:
        manager = create_mcp_manager()
        statuses = await manager.start()
        for server_id, status in statuses.items():
            if not status.enabled or status.connection_state != "ready":
                continue
            try:
                await manager.discover(server_id)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                get_logger(
                    error_type=type(error).__name__,
                    server_id=server_id,
                ).error("mcp.discovery_failed")
        application = create_application()
        registry = application.runtime.tools
        refresh = getattr(registry, "refresh_from_manager", None)
        if callable(refresh):
            refresh(manager)
        skill_registry = getattr(application.context, "skill_registry", None)
        reload_skills = getattr(skill_registry, "reload", None)
        if callable(reload_skills):
            reload_skills()
    except asyncio.CancelledError:
        raise
    except Exception as error:
        get_logger(error_type=type(error).__name__).error("mcp.startup_failed")
    try:
        yield
    finally:
        if manager is not None:
            try:
                await manager.shutdown()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                get_logger(error_type=type(error).__name__).error(
                    "mcp.shutdown_failed"
                )
        await create_application().wait_for_background_tasks()


def create_api() -> FastAPI:
    api = FastAPI(
        title="Starter Agent API", version="0.1.0", lifespan=_api_lifespan
    )
    api.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:8001",
            "http://localhost:8001",
        ],
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "If-Match", "Idempotency-Key", "Authorization"],
    )
    api.include_router(create_capabilities_router())
    api.include_router(create_trust_router())
    active_chat_tasks: set[asyncio.Task] = set()

    @api.get("/health")
    async def health() -> dict[str, object]:
        application = create_application()
        active_revision = application.runtime_revision
        desired_revision = active_revision
        return {
            "status": "ok",
            "name": get_settings().app.name,
            "runtime_revision": active_revision.id,
            "desired_runtime_revision": desired_revision.id,
            "restart_required": active_revision.requires_restart(
                desired_revision
            ),
        }

    @api.get("/v1/providers", response_model=ProvidersResponse)
    async def providers() -> ProvidersResponse:
        settings = get_settings()
        infos: list[ProviderInfo] = []
        for name in sorted(settings.providers):
            config = settings.providers[name]
            infos.append(
                ProviderInfo(
                    name=name,
                    type=config.type,
                    models=config.models,
                    is_default=name == settings.model.default_provider,
                    has_api_key=(
                        True
                        if config.type == "mock"
                        else bool(settings.provider_api_key(name))
                    ),
                )
            )
        return ProvidersResponse(
            default_provider=settings.model.default_provider,
            default_model=settings.model.default_model,
            providers=infos,
        )

    @api.get(
        "/v1/tools",
        response_model=ToolsResponse,
        response_model_exclude_none=True,
    )
    async def tools() -> ToolsResponse:
        registry = create_application().runtime.tools
        catalog_reader = getattr(registry, "lightweight_catalog", None)
        if callable(catalog_reader):
            builtin_tools = {tool.name: tool for tool in registry.list()}
            return ToolsResponse(
                tools=[
                    ToolInfo(
                        name=item.name,
                        description=(
                            builtin_tools[item.name].description
                            if item.type == "builtin"
                            else None
                        ),
                        risk_level=(
                            builtin_tools[item.name].risk_level
                            if item.type == "builtin"
                            else None
                        ),
                        source=item.type,
                        server=item.server,
                        type=item.type,
                        enabled=item.enabled,
                        review=item.review,
                        callable=item.callable,
                    )
                    for item in catalog_reader().capabilities
                ]
            )
        return ToolsResponse(
            tools=[
                ToolInfo(
                    name=tool.name,
                    description=tool.description,
                    risk_level=tool.risk_level,
                )
                for tool in registry.list()
            ]
        )

    @api.get("/v1/knowledge-bases")
    async def list_knowledge_bases() -> dict[str, object]:
        bases = create_knowledge_service().list_knowledge_bases()
        return {
            "knowledge_bases": [
                item.model_dump(mode="json") for item in bases
            ]
        }

    @api.post(
        "/v1/knowledge-bases/{knowledge_base_id}/documents",
        status_code=202,
    )
    async def upload_knowledge_document(
        knowledge_base_id: UUID,
        file: UploadFile = File(...),
        document_type: str = Form("other"),
        confirmed_authorized: bool = Form(False),
    ) -> dict[str, object]:
        try:
            content = await file.read(
                get_settings().knowledge.max_upload_bytes + 1
            )
            result = create_knowledge_service().upload(
                knowledge_base_id=knowledge_base_id,
                filename=file.filename or "",
                content=content,
                document_type=document_type,
                confirmed_authorized=confirmed_authorized,
            )
        except KnowledgeError as error:
            raise _knowledge_http_error(error) from error
        return {
            "document_id": str(result.document.id),
            "version_id": str(result.version.id),
            "job_id": str(result.job.id),
            "status": result.job.status,
            "stage": result.job.stage,
            "content_sha256": result.version.content_sha256,
        }

    @api.get("/v1/knowledge-bases/{knowledge_base_id}/documents")
    async def list_knowledge_documents(
        knowledge_base_id: UUID,
    ) -> dict[str, object]:
        documents = create_knowledge_service().list_documents(
            knowledge_base_id
        )
        return {
            "documents": [
                item.model_dump(mode="json") for item in documents
            ]
        }

    @api.get(
        "/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}"
    )
    async def get_knowledge_document(
        knowledge_base_id: UUID,
        document_id: UUID,
    ) -> dict[str, object]:
        try:
            document = create_knowledge_service().get_document(
                knowledge_base_id, document_id
            )
        except KnowledgeError as error:
            raise _knowledge_http_error(error) from error
        return document.model_dump(mode="json")

    @api.get(
        "/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/chunks"
    )
    async def list_knowledge_chunks(
        knowledge_base_id: UUID,
        document_id: UUID,
        after_ordinal: int = Query(default=-1, ge=-1),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> dict[str, object]:
        try:
            chunks = create_knowledge_service().list_chunks(
                knowledge_base_id,
                document_id,
                after_ordinal=after_ordinal,
                limit=limit,
            )
        except KnowledgeError as error:
            raise _knowledge_http_error(error) from error
        return {
            "chunks": [
                {
                    **item.model_dump(
                        mode="json",
                        exclude={"text", "search_text"},
                    ),
                    "source_ref": item.source_ref,
                    "preview": item.text[:400],
                }
                for item in chunks
            ],
            "next_after_ordinal": (
                chunks[-1].ordinal if len(chunks) == limit else None
            ),
        }

    @api.post("/v1/knowledge-bases/{knowledge_base_id}/retrieve")
    async def retrieve_knowledge(
        knowledge_base_id: UUID,
        request: KnowledgeRetrieveRequest,
    ) -> dict[str, object]:
        try:
            matches = create_knowledge_service().retrieve(
                knowledge_base_id,
                request.question,
                top_k=request.top_k,
                document_ids=request.document_ids,
                document_types=request.document_types,
                filenames=request.filenames,
                versions=request.versions,
            )
        except KnowledgeError as error:
            raise _knowledge_http_error(error) from error
        return {
            "status": "ok" if matches else "no_evidence",
            "matches": [
                item.model_dump(mode="json") for item in matches
            ],
        }

    @api.post("/v1/knowledge-bases/{knowledge_base_id}/answer")
    async def answer_from_knowledge(
        knowledge_base_id: UUID,
        request: KnowledgeAnswerRequest,
    ) -> dict[str, object]:
        try:
            answer = await create_knowledge_service().answer(
                knowledge_base_id,
                request.question,
                provider_name=request.provider,
                model=request.model,
            )
        except KnowledgeError as error:
            raise _knowledge_http_error(error) from error
        return answer.model_dump(mode="json")

    @api.put(
        "/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/content",
        status_code=202,
    )
    async def update_knowledge_document(
        knowledge_base_id: UUID,
        document_id: UUID,
        file: UploadFile = File(...),
        confirmed_authorized: bool = Form(False),
        if_match: str = Header(..., alias="If-Match"),
    ) -> dict[str, object]:
        try:
            content = await file.read(
                get_settings().knowledge.max_upload_bytes + 1
            )
            result = create_knowledge_service().update_document(
                knowledge_base_id,
                document_id,
                expected_content_sha256=if_match.strip('"'),
                filename=file.filename or "",
                content=content,
                confirmed_authorized=confirmed_authorized,
            )
        except KnowledgeError as error:
            raise _knowledge_http_error(error) from error
        return {
            "document_id": str(result.document.id),
            "version_id": str(result.version.id),
            "job_id": str(result.job.id),
            "version": result.version.version,
            "content_sha256": result.version.content_sha256,
            "status": "queued",
        }

    @api.delete(
        "/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}"
    )
    async def delete_knowledge_document(
        knowledge_base_id: UUID,
        document_id: UUID,
    ) -> dict[str, object]:
        deleted = create_knowledge_service().delete_document(
            knowledge_base_id, document_id
        )
        return {"status": "deleted", "existed": deleted}

    @api.get(
        "/v1/knowledge-bases/{knowledge_base_id}/citations/{chunk_id}"
    )
    async def resolve_knowledge_citation(
        knowledge_base_id: UUID,
        chunk_id: UUID,
    ) -> dict[str, object]:
        try:
            chunk = create_knowledge_service().resolve_citation(
                knowledge_base_id, chunk_id
            )
        except KnowledgeError as error:
            raise _knowledge_http_error(error) from error
        return {
            **chunk.model_dump(
                mode="json", exclude={"text", "search_text"}
            ),
            "source_ref": chunk.source_ref,
            "quote": chunk.text[:400],
        }

    @api.get(
        "/v1/knowledge-bases/{knowledge_base_id}/ingestion-jobs/{job_id}"
    )
    async def get_knowledge_ingestion_job(
        knowledge_base_id: UUID,
        job_id: UUID,
    ) -> dict[str, object]:
        try:
            job = create_knowledge_service().get_job(
                knowledge_base_id, job_id
            )
        except KnowledgeError as error:
            raise _knowledge_http_error(error) from error
        return job.model_dump(mode="json")

    @api.post(
        "/v1/email/drafts/{draft_id}/approval-challenges",
        response_model=ApprovalChallengeView,
    )
    async def create_email_approval_challenge(
        draft_id: str,
        request: EmailApprovalChallengeRequest,
    ) -> ApprovalChallengeView:
        try:
            return _email_approval_service().create_challenge(
                draft_id,
                session_id=str(request.session_id),
                profile=request.profile,
                user_ref=request.user_ref,
            )
        except EmailError as error:
            raise _email_http_error(error) from error

    @api.post(
        "/v1/email/approval-challenges/{approval_id}/confirm",
        response_model=SendApproval,
    )
    async def confirm_email_approval(
        approval_id: str,
        request: EmailApprovalActionRequest,
    ) -> SendApproval:
        try:
            approval = _email_approval_service().confirm(
                approval_id,
                session_id=str(request.session_id),
                confirmed=request.confirmed,
            )
            application = create_application()
            manager = application.runtime.tools.email_manager
            assert manager is not None
            draft = manager.store.get_draft(
                approval.draft_id,
                session_id=str(request.session_id),
                profile=approval.profile,
            )
            gate_request = _email_gate_request(application, approval, draft)
            confirmation = Confirmation(
                id=f"email-{approval.approval_id}",
                principal=approval.user_ref or "local-user",
                session_id=approval.session_id,
                turn_id=gate_request.turn_id,
                call_id=gate_request.call_id,
                request_hash=gate_request.confirmation_request_hash,
                server_id=gate_request.server_id,
                tool_name=gate_request.tool_name,
                schema_hash=gate_request.schema_hash,
                snapshot_id=gate_request.snapshot_id,
                arguments_hash=gate_request.confirmation_arguments_hash,
                arguments_summary={
                    "draft_id": draft.draft_id,
                    "content_sha256": draft.content_sha256,
                    "recipient_sha256": approval.recipient_sha256,
                    "attachment_sha256s": approval.attachment_sha256s,
                },
                risk="external",
                destination="email",
                decision="once",
                status="approved",
                expires_at=approval.expires_at,
                idempotency_key_hash=canonical_json_sha256(
                    {"approval_id": approval.approval_id}
                ),
                decided_at=approval.approved_at or datetime.now(UTC),
            )
            try:
                application.runtime.gate.store.create_confirmation(confirmation)
            except RecordAlreadyExistsError:
                existing = application.runtime.gate.store.get_confirmation(
                    confirmation.id
                )
                if existing is None or existing.request_hash != confirmation.request_hash:
                    raise EmailError(
                        EmailErrorCode.APPROVAL_REQUIRED,
                        "approval binding conflict",
                    )
            return approval
        except EmailError as error:
            raise _email_http_error(error) from error

    @api.get(
        "/v1/email/approvals/{approval_id}",
        response_model=SendApproval,
    )
    async def get_email_approval(
        approval_id: str,
        session_id: UUID = Query(),
    ) -> SendApproval:
        try:
            return _email_approval_service().get(
                approval_id, session_id=str(session_id)
            )
        except EmailError as error:
            raise _email_http_error(error) from error

    @api.post(
        "/v1/email/approvals/{approval_id}/revoke",
        response_model=SendApproval,
    )
    async def revoke_email_approval(
        approval_id: str,
        request: EmailApprovalActionRequest,
    ) -> SendApproval:
        try:
            return _email_approval_service().revoke(
                approval_id, session_id=str(request.session_id)
            )
        except EmailError as error:
            raise _email_http_error(error) from error

    @api.post(
        "/v1/email/approvals/{approval_id}/send",
        response_model=ToolResult,
    )
    async def send_approved_email(
        approval_id: str,
        request: EmailApprovalSendRequest,
    ) -> ToolResult:
        application = create_application()
        tool = application.runtime.tools.get("email_send")
        manager = application.runtime.tools.email_manager
        if tool is None or manager is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error_code": "email_tools_not_enabled",
                    "message": "邮件发送工具尚未启用",
                },
            )
        try:
            application.runtime.policy.check(tool)
            approval = EmailApprovalService(manager).get(
                approval_id,
                session_id=str(request.session_id),
            )
            if approval.status not in {"approved", "consumed"}:
                raise EmailError(
                    EmailErrorCode.APPROVAL_REQUIRED,
                    "email approval is required",
                )
            draft = manager.store.get_draft(
                approval.draft_id,
                session_id=str(request.session_id),
                profile=approval.profile,
            )
            capability_confirmation = application.runtime.gate.store.get_confirmation(
                f"email-{approval.approval_id}"
            )
            execution_key_hash = hashlib.sha256(
                request.idempotency_key.encode("utf-8")
            ).hexdigest()
            if (
                capability_confirmation is not None
                and capability_confirmation.status == "consumed"
            ):
                if (
                    capability_confirmation.execution_idempotency_key_hash
                    != execution_key_hash
                ):
                    raise EmailError(
                        EmailErrorCode.APPROVAL_CONSUMED,
                        "email approval has already been consumed",
                    )
                receipt = manager.store.find_receipt(
                    draft.draft_id, request.idempotency_key
                )
                if receipt is None:
                    raise EmailError(
                        EmailErrorCode.APPROVAL_CONSUMED,
                        "email approval receipt is unavailable",
                    )
                result = ToolResult(
                    ok=True,
                    data=receipt.model_dump(mode="json"),
                    display=(
                        "Mock 发送流程已完成；没有邮件对外发送"
                        if not receipt.external_delivery
                        else "邮件已由 SMTP provider 确认发送"
                    ),
                    metadata={
                        "profile": approval.profile,
                        "delivery_mode": receipt.delivery_mode,
                        "external_delivery": receipt.external_delivery,
                        "status": receipt.status,
                        "source_ref": receipt.source_ref,
                    },
                )
            else:
                result = await application.runtime.execute_tool(
                    tool_name=tool.name,
                    arguments={
                        "profile": approval.profile,
                        "draft_id": draft.draft_id,
                        "expected_content_sha256": draft.content_sha256,
                        "approval_id": approval.approval_id,
                        "idempotency_key": request.idempotency_key,
                    },
                    session_id=request.session_id,
                    turn_id=_email_turn_id(approval.approval_id),
                    call_id=f"api-email-send-{approval.approval_id}",
                    principal=approval.user_ref or "local-user",
                    confirmation_id=f"email-{approval.approval_id}",
                )
        except EmailError as error:
            raise _email_http_error(error) from error
        except AgentError as error:
            raise HTTPException(
                status_code=error.http_status,
                detail=error.to_public_dict(),
            ) from error
        except ToolExecutionDenied as error:
            raise HTTPException(
                status_code=409,
                detail={"error_code": error.code},
            ) from error
        if not result.ok:
            raise HTTPException(
                status_code=400,
                detail=result.model_dump(mode="json", exclude_none=True),
            )
        get_logger(
            session_id=str(request.session_id),
            approval_id=approval_id,
        ).info(
            "email.manual_send_completed",
            status=result.metadata.get("status"),
            external_delivery=result.metadata.get("external_delivery"),
        )
        return result

    @api.post("/v1/chat", response_model=ChatResult)
    async def chat(request: ChatRequest) -> ChatResult:
        try:
            application = create_application()
            route = await _classify_chat_request(request, application)
            return await _dispatch_classified_chat(
                request,
                application=application,
                route=route,
            )
        except KnowledgeError as exc:
            raise _knowledge_http_error(exc) from exc
        except AgentError as exc:
            raise HTTPException(
                status_code=exc.http_status,
                detail=exc.to_public_dict(),
            ) from exc

    @api.post("/v1/chat/stream")
    async def chat_stream(request: ChatRequest) -> StreamingResponse:
        try:
            application = create_application()
            route = await _classify_chat_request(request, application)
        except KnowledgeError as exc:
            raise _knowledge_http_error(exc) from exc
        except AgentError as exc:
            raise HTTPException(
                status_code=exc.http_status,
                detail=exc.to_public_dict(),
            ) from exc

        use_buffered_route = (
            route.route is not KnowledgeRequestRoute.KNOWLEDGE_QUERY
            or request.knowledge_mode == "required"
            or (
                request.knowledge_mode == "auto"
                and request.knowledge_base_id is not None
            )
        )
        if use_buffered_route:
            async def knowledge_events():
                try:
                    result = await _dispatch_classified_chat(
                        request,
                        application=application,
                        route=route,
                    )
                    yield (
                        "data: "
                        + json.dumps(
                            {
                                "type": "delta",
                                "content": result.content,
                            },
                            ensure_ascii=False,
                        )
                        + "\n\n"
                    )
                    yield (
                        "data: "
                        + json.dumps(
                            {
                                "type": "done",
                                "result": result.model_dump(mode="json"),
                            },
                            ensure_ascii=False,
                        )
                        + "\n\n"
                    )
                except HTTPException as exc:
                    yield (
                        "data: "
                        + json.dumps(
                            {"type": "error", "error": exc.detail},
                            ensure_ascii=False,
                        )
                        + "\n\n"
                    )
                except KnowledgeError as exc:
                    yield (
                        "data: "
                        + json.dumps(
                            {
                                "type": "error",
                                "error": _knowledge_http_error(exc).detail,
                            },
                            ensure_ascii=False,
                        )
                        + "\n\n"
                    )
                except AgentError as exc:
                    yield (
                        "data: "
                        + json.dumps(
                            {"type": "error", "error": exc.to_public_dict()},
                            ensure_ascii=False,
                        )
                        + "\n\n"
                    )
            return StreamingResponse(
                knowledge_events(), media_type="text/event-stream"
            )

        async def events():
            queue: asyncio.Queue[dict | None] = asyncio.Queue()

            async def on_delta(text: str) -> None:
                await queue.put({"type": "delta", "content": text})

            async def on_tool_event(event: dict) -> None:
                await queue.put(event)

            async def run_chat() -> None:
                try:
                    result = await application.chat(
                        content=request.message,
                        session_id=request.session_id,
                        provider_name=request.provider,
                        model=request.model,
                        on_delta=on_delta,
                        required_tool_name=request.tool,
                        on_tool_event=on_tool_event,
                        tool_governance_enabled=request.tool_governance_enabled,
                    )
                    await queue.put({"type": "done", "result": result.model_dump(mode="json")})
                except AgentError as exc:
                    await queue.put(
                        {
                            "type": "error",
                            "error": exc.to_public_dict(),
                        }
                    )
                finally:
                    await queue.put(None)

            task = asyncio.create_task(run_chat())
            active_chat_tasks.add(task)
            task.add_done_callback(active_chat_tasks.discard)
            try:
                while True:
                    event = await queue.get()
                    if event is None:
                        break
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            finally:
                if task.done():
                    await task
                else:
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        # A browser refresh closes only this SSE transport. The
                        # persisted turn remains live so the same session can
                        # recover and decide its pending confirmation.
                        pass

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @api.get("/v1/sessions", response_model=SessionListResponse)
    async def sessions(
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> SessionListResponse:
        application = create_application()
        rows = application.list_sessions(limit=limit, offset=offset)
        total = application.count_sessions()
        return SessionListResponse(
            total=total,
            offset=offset,
            limit=limit,
            has_more=offset + len(rows) < total,
            sessions=[
                SessionSummary(
                    id=row.id,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                    title=_summary_text(row.first_user_message),
                    message_count=row.message_count,
                    last_message=_summary_text(row.last_message),
                )
                for row in rows
            ]
        )

    @api.get(
        "/v1/sessions/{session_id}/messages",
        response_model=SessionMessagesResponse,
    )
    async def session_messages(
        session_id: UUID,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> SessionMessagesResponse:
        application = create_application()
        try:
            rows = application.list_session_messages(
                session_id=session_id,
                limit=limit,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        session_usage = application.session_usage(session_id)
        return SessionMessagesResponse(
            session_id=session_id,
            session_usage=session_usage,
            max_total_tokens=get_settings().context.max_total_tokens,
            token_budget_status=application.token_budget_status(
                session_usage.total_tokens
            ),
            latest_summary_trace=application.latest_summary_trace(session_id),
            messages=[
                HistoryMessage(
                    role=row.role,
                    content=row.content,
                    name=row.name,
                    tool_call_id=row.tool_call_id,
                    created_at=row.created_at,
                    turn_id=row.turn_id,
                )
                for row in rows
            ],
        )

    @api.delete("/v1/sessions/{session_id}")
    async def delete_session(session_id: UUID) -> dict[str, str]:
        deleted = create_application().delete_session(session_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Session not found")
        return {"status": "deleted"}

    @api.delete("/v1/sessions")
    async def delete_all_sessions() -> dict[str, int | str]:
        application = create_application()
        await application.wait_for_background_tasks()
        deleted = application.delete_all_sessions()
        get_logger().info("sessions.deleted_all", deleted_sessions=deleted)
        return {"status": "deleted", "deleted_sessions": deleted}

    @api.get("/v1/memories", response_model=MemoryListResponse)
    async def list_memories(
        active_only: bool = Query(default=False),
    ) -> MemoryListResponse:
        return MemoryListResponse(
            memories=create_application().list_memories(active_only=active_only)
        )

    @api.post("/v1/memories", response_model=MemoryItem, status_code=201)
    async def create_memory(request: MemoryCreateRequest) -> MemoryItem:
        _validate_memory_write(
            source_type=request.source_type,
            confirmed=request.confirmed,
            expires_at=request.expires_at,
        )
        item = create_application().create_memory(
            key=request.key.strip(),
            value=request.value.strip(),
            category=request.category,
            source_ref="user:memory-panel",
            source_type="user_confirmed",
            confidence=1.0,
            verified_by="user",
            expires_at=_memory_expiry(request.category, request.expires_at),
            sensitivity=request.sensitivity,
        )
        get_logger(memory_id=str(item.id)).info(
            "memory.created",
            category=item.category,
            source_type=item.source_type,
            expires_at=item.expires_at.isoformat() if item.expires_at else None,
        )
        return item

    @api.put("/v1/memories/{memory_id}", response_model=MemoryItem)
    async def update_memory(
        memory_id: UUID, request: MemoryUpdateRequest
    ) -> MemoryItem:
        _validate_memory_write(
            source_type="user_confirmed",
            confirmed=request.confirmed,
            expires_at=request.expires_at,
        )
        item = create_application().update_memory(
            memory_id,
            key=request.key.strip(),
            value=request.value.strip(),
            category=request.category,
            source_ref="user:memory-panel:update",
            confidence=1.0,
            expires_at=_memory_expiry(request.category, request.expires_at),
            sensitivity=request.sensitivity,
            status=request.status,
        )
        if item is None:
            raise HTTPException(status_code=404, detail="Memory not found")
        get_logger(memory_id=str(item.id)).info(
            "memory.updated",
            category=item.category,
            status=item.status,
        )
        return item

    @api.delete("/v1/memories/{memory_id}")
    async def delete_memory(memory_id: UUID) -> dict[str, str]:
        if not create_application().delete_memory(memory_id):
            raise HTTPException(status_code=404, detail="Memory not found")
        # The audit event deliberately contains only the ID, never the memory value.
        get_logger(memory_id=str(memory_id)).info("memory.deleted")
        return {"status": "deleted", "id": str(memory_id)}

    return api


app = create_api()
