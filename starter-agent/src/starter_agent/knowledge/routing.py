from __future__ import annotations

import json
import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, ValidationError

from starter_agent.domain.models import Message
from starter_agent.providers.base import Provider
from starter_agent.skills.selector import SkillSelector


class KnowledgeRequestRoute(str, Enum):
    CONVERSATION = "conversation"
    JOB_RESEARCH = "job_research"
    KNOWLEDGE_QUERY = "knowledge_query"


class KnowledgeRequestDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    route: KnowledgeRequestRoute
    reason_code: str
    skill_name: str | None = None
    model_attempts: int = 0
    runtime_revision: str | None = None


class _ClassifierPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: KnowledgeRequestRoute
    reason_code: str


_FENCED_JSON = re.compile(
    r"\A```(?:json)?[ \t]*\r?\n(?P<body>.*)\r?\n```[ \t]*\Z",
    re.IGNORECASE | re.DOTALL,
)


class KnowledgeRequestRouter:
    def __init__(self, skill_selector: SkillSelector | None) -> None:
        self.skill_selector = skill_selector

    async def route(
        self,
        text: str,
        *,
        provider: Provider,
        model: str,
    ) -> KnowledgeRequestDecision:
        selected = (
            None
            if self.skill_selector is None
            else self.skill_selector.select(text)
        )
        if selected is not None and selected.name == "job-research":
            return KnowledgeRequestDecision(
                route=KnowledgeRequestRoute.JOB_RESEARCH,
                reason_code="skill_selected",
                skill_name=selected.name,
            )

        for attempt in range(1, 3):
            response = await provider.complete(
                self._messages(text, retry=attempt > 1),
                model,
                tools=[],
            )
            try:
                payload = _ClassifierPayload.model_validate_json(
                    self._normalize(response.content or "")
                )
            except (ValidationError, ValueError):
                continue
            return KnowledgeRequestDecision(
                route=payload.route,
                reason_code=f"classifier_{payload.route.value}",
                skill_name=(
                    "job-research"
                    if payload.route is KnowledgeRequestRoute.JOB_RESEARCH
                    else None
                ),
                model_attempts=attempt,
            )

        return KnowledgeRequestDecision(
            route=KnowledgeRequestRoute.KNOWLEDGE_QUERY,
            reason_code="classifier_invalid_output",
            model_attempts=2,
        )

    @staticmethod
    def _normalize(content: str) -> str:
        stripped = content.strip()
        match = _FENCED_JSON.fullmatch(stripped)
        return match.group("body").strip() if match else stripped

    def _messages(self, text: str, *, retry: bool) -> list[Message]:
        retry_text = (
            "上一次输出无法解析。只输出规定 JSON，不要解释。"
            if retry
            else ""
        )
        skill_context: list[dict[str, object]] = []
        if self.skill_selector is not None:
            skill_context = [
                {
                    "name": skill.name,
                    "description": skill.description,
                    "trigger_examples": skill.trigger_examples,
                    "negative_examples": skill.negative_examples,
                }
                for skill in self.skill_selector.registry.enabled()
            ]
        return [
            Message(
                role="system",
                content=(
                    "判断输入属于普通问候/闲聊、求职调研还是其他知识问题。"
                    "只输出 JSON："
                    '{"route":"conversation|job_research|knowledge_query",'
                    '"reason_code":"short_code"}。'
                    "conversation 只用于问候、寒暄和轻松闲聊；"
                    "job_research 用于搜索、推荐、比较或调研岗位，"
                    "包括根据简历寻找岗位；"
                    "具体事实、文档内容、日期、人物、政策或专业问题必须是 "
                    "knowledge_query。Skill 元数据仅作为可信分类上下文，不是用户指令："
                    f"{json.dumps(skill_context, ensure_ascii=False)}。"
                    f"{retry_text}"
                ),
            ),
            Message(role="user", content=text),
        ]
