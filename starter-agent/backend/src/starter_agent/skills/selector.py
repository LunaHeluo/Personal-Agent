from __future__ import annotations

import re

from starter_agent.skills.models import SkillDefinition
from starter_agent.skills.registry import SkillRegistry


_JOB_RESEARCH_POSITIVE = re.compile(
    r"(?:搜索|查找|寻找|找).{0,30}(?:岗位|职位|工作|招聘)|"
    r"(?:读取|打开|分析|研究).{0,12}(?:JD|职位描述|岗位描述)|"
    r"(?:JD|职位描述|岗位描述).{0,20}(?:简历|匹配|比较)|"
    r"(?:compare|analy[sz]e|read|research).{0,20}(?:job description|\bJD\b)|"
    r"(?:search|find).{0,12}(?:jobs?|roles?|openings?)",
    re.IGNORECASE,
)
_JOB_RESEARCH_NEGATIVE = re.compile(
    r"(?:通用|一般).{0,8}(?:求职|面试).{0,8}(?:建议|技巧)|"
    r"(?:只|仅).{0,6}(?:改写|润色|重写|翻译)|"
    r"(?:general).{0,12}(?:career|job).{0,8}(?:advice|tips)|"
    r"(?:only|just).{0,8}(?:rewrite|polish|translate)",
    re.IGNORECASE,
)


class SkillSelector:
    def __init__(self, registry: SkillRegistry) -> None:
        self.registry = registry

    def select(self, text: str) -> SkillDefinition | None:
        normalized = " ".join(text.split())
        if not normalized:
            return None
        for skill in self.registry.enabled():
            if self._negative_match(skill, normalized):
                continue
            if skill.name == "job-research":
                if _JOB_RESEARCH_NEGATIVE.search(normalized):
                    continue
                if _JOB_RESEARCH_POSITIVE.search(normalized):
                    return skill
            if any(
                example.casefold() in normalized.casefold()
                for example in skill.trigger_examples
            ):
                return skill
        return None

    @staticmethod
    def _negative_match(skill: SkillDefinition, text: str) -> bool:
        folded = text.casefold()
        return any(
            example.casefold() in folded
            for example in skill.negative_examples
        )
