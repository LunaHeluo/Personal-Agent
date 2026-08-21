from __future__ import annotations

import difflib
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from starter_agent.domain.models import ToolResult
from starter_agent.tools.base import Tool, ToolContext


SUPPORTED_SUFFIXES = {".md", ".txt"}
MAX_RESUME_CHARS = 100_000
MAX_DIFF_CHARS = 6_000
MAX_JOB_DESCRIPTION_CHARS = 50_000


# The matcher is intentionally deterministic: it finds traceable evidence instead
# of asking a model to invent a score. Aliases only establish that two phrases
# refer to the same job-search concept; the returned resume evidence remains
# verbatim text from the user's file.
MATCH_CONCEPTS: dict[str, tuple[str, ...]] = {
    "agent": ("agent", "智能体"),
    "backend": ("backend", "back-end", "后端", "api", "fastapi"),
    "context": ("context", "上下文"),
    "developer_tools": (
        "developer tool",
        "developer service",
        "ide",
        "code assistant",
        "coding",
        "开发者工具",
        "开发者服务",
        "代码助手",
        "研发平台",
    ),
    "education": (
        "bachelor",
        "master",
        "graduate",
        "university",
        "本科",
        "硕士",
        "学历",
        "计算机",
    ),
    "evaluation": (
        "evaluation",
        "evaluate",
        "benchmark",
        "experiment",
        "metric",
        "评测",
        "评估",
        "实验",
        "指标",
        "可观测",
    ),
    "llm": ("llm", "large language model", "大模型", "gpt", "t5", "bert"),
    "mcp": ("mcp",),
    "prompt": ("prompt", "提示词"),
    "python": ("python",),
    "rag": ("rag", "retrieval-augmented", "检索增强", "向量数据库"),
    "reliability": (
        "reliability",
        "reliable",
        "maintainable",
        "安全边界",
        "可靠性",
        "可维护",
    ),
    "testing": ("test", "testing", "unit test", "测试"),
    "tool_calling": ("tool calling", "tool call", "工具调用"),
    "workflow": ("workflow", "工作流", "流程"),
}


class ResumeManager:
    def __init__(
        self,
        project_root: Path,
        storage_root: Path | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.storage_root = (storage_root or self.project_root).resolve()
        self.drafts_root = self.storage_root / "drafts"
        self.versions_root = self.storage_root / "versions"
        self.manifest_path = self.storage_root / "versions.json"

    def read(self, value: str) -> tuple[Path, str, str]:
        path = self._resolve_path(value)
        if not path.exists() or not path.is_file():
            raise ResumeToolFailure("file_not_found", "没有找到指定的简历文件")
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise ResumeToolFailure(
                "unsupported_format", "最小版本目前只支持 Markdown 和 TXT 简历"
            )
        text = path.read_text(encoding="utf-8")
        if len(text) > MAX_RESUME_CHARS:
            raise ResumeToolFailure("file_too_large", "简历内容过长，无法安全处理")
        return path, text, sha256_text(text)

    def read_job(self, job_id: str) -> tuple[Path, dict[str, Any], str]:
        normalized = job_id.strip()
        if not normalized:
            raise ResumeToolFailure("missing_job_description", "请选择具体岗位或提供完整 JD")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", normalized):
            raise ResumeToolFailure("invalid_job_id", "岗位 ID 格式不正确")
        jobs_root = (self.storage_root / "jobs").resolve()
        try:
            jobs_root.relative_to(self.storage_root)
        except ValueError as exc:  # pragma: no cover - defensive path invariant
            raise ResumeToolFailure("path_not_allowed", "岗位目录不在允许范围内") from exc
        if not jobs_root.exists():
            raise ResumeToolFailure("job_not_found", "没有找到指定的岗位资料")
        for path in sorted(jobs_root.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                if path.stem == normalized or path.stem.startswith(f"{normalized}_"):
                    raise ResumeToolFailure("job_parse_failed", "岗位资料无法解析") from exc
                continue
            if isinstance(payload, dict) and str(payload.get("id", "")) == normalized:
                canonical = json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                return path, payload, sha256_text(canonical)
        raise ResumeToolFailure("job_not_found", "没有找到指定的岗位资料")

    def relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.storage_root).as_posix()

    def save_resume(
        self,
        filename: str,
        content: str,
        confirmed: bool,
    ) -> dict[str, Any]:
        if not confirmed:
            raise ResumeToolFailure(
                "approval_required", "保存简历文件前需要用户明确确认"
            )
        safe_name = Path(filename).name.strip()
        if not safe_name or safe_name != filename.strip():
            raise ResumeToolFailure(
                "invalid_filename", "文件名不能包含目录或路径跳转"
            )
        suffix = Path(safe_name).suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise ResumeToolFailure(
                "unsupported_format", "简历只支持 Markdown 或 TXT 文件"
            )
        if not content.strip():
            raise ResumeToolFailure("empty_resume", "简历内容不能为空")
        if len(content) > MAX_RESUME_CHARS:
            raise ResumeToolFailure("file_too_large", "简历内容过长，无法安全保存")
        self.storage_root.mkdir(parents=True, exist_ok=True)
        path = (self.storage_root / safe_name).resolve()
        if path.exists():
            raise ResumeToolFailure(
                "file_already_exists",
                "同名简历已经存在；请使用新文件名，现有文件不会被覆盖",
            )
        with path.open("x", encoding="utf-8") as stream:
            stream.write(content)
        return {
            "path": self.relative(path),
            "sha256": sha256_text(content),
            "format": suffix.lstrip("."),
            "characters": len(content),
        }

    def list_resumes(self) -> dict[str, Any]:
        self.storage_root.mkdir(parents=True, exist_ok=True)
        files = [
            {
                "path": self.relative(path),
                "format": path.suffix.lower().lstrip("."),
                "characters": path.stat().st_size,
                "modified_at": datetime.fromtimestamp(
                    path.stat().st_mtime, tz=UTC
                ).isoformat(),
            }
            for path in sorted(self.storage_root.glob("*"))
            if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
        ]
        versions = self._load_manifest()
        return {
            "root": self.storage_root.as_posix(),
            "files": files,
            "versions": versions,
        }

    def create_draft(
        self,
        resume_path: str,
        target_text: str,
        replacement_text: str,
        evidence: list[str],
    ) -> dict[str, Any]:
        path, original, base_sha256 = self.read(resume_path)
        if not target_text or original.count(target_text) != 1:
            raise ResumeToolFailure(
                "target_not_unique", "待修改内容必须在原简历中准确出现一次"
            )
        if not replacement_text.strip():
            raise ResumeToolFailure("invalid_patch", "替换内容不能为空")
        if not evidence or any(item not in original for item in evidence):
            raise ResumeToolFailure(
                "missing_evidence", "每条修改依据都必须是原简历中的真实文本片段"
            )
        proposed = original.replace(target_text, replacement_text, 1)
        diff, truncated = unified_diff(
            original, proposed, self.relative(path), "draft"
        )
        draft_id = str(uuid4())
        payload = {
            "draft_id": draft_id,
            "source_path": self.relative(path),
            "base_sha256": base_sha256,
            "proposed_sha256": sha256_text(proposed),
            "proposed_content": proposed,
            "evidence": evidence,
            "created_at": datetime.now(UTC).isoformat(),
        }
        self.drafts_root.mkdir(parents=True, exist_ok=True)
        draft_path = self.drafts_root / f"{draft_id}.json"
        draft_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {
            "draft_id": draft_id,
            "source_path": payload["source_path"],
            "base_sha256": base_sha256,
            "proposed_sha256": payload["proposed_sha256"],
            "diff": diff,
            "diff_truncated": truncated,
            "requires_approval": True,
        }

    def save_version(
        self,
        draft_id: str,
        base_sha256: str,
        confirmed: bool,
        label: str,
        context: ToolContext,
    ) -> dict[str, Any]:
        if not confirmed:
            raise ResumeToolFailure(
                "approval_required", "保存简历新版本前需要用户明确确认"
            )
        if not re.fullmatch(r"[0-9a-f-]{36}", draft_id):
            raise ResumeToolFailure("draft_not_found", "没有找到指定的简历草稿")
        draft_path = self.drafts_root / f"{draft_id}.json"
        if not draft_path.exists():
            raise ResumeToolFailure("draft_not_found", "没有找到指定的简历草稿")
        payload = json.loads(draft_path.read_text(encoding="utf-8"))
        if payload.get("base_sha256") != base_sha256:
            raise ResumeToolFailure(
                "resume_version_conflict", "草稿基于的简历版本与请求不一致"
            )
        source, _, current_sha256 = self.read(str(payload["source_path"]))
        if current_sha256 != base_sha256:
            raise ResumeToolFailure(
                "resume_version_conflict", "原简历已经变化，请重新读取并生成补丁"
            )
        proposed = str(payload["proposed_content"])
        proposed_sha256 = sha256_text(proposed)
        if proposed_sha256 != payload.get("proposed_sha256"):
            raise ResumeToolFailure("draft_corrupted", "简历草稿校验失败")

        safe_label = re.sub(r"[^A-Za-z0-9_-]+", "-", label.strip())[:40].strip("-")
        safe_label = safe_label or "resume"
        family_dir = self.versions_root / source.stem
        family_dir.mkdir(parents=True, exist_ok=True)
        versions = self._load_manifest()
        source_relative = self.relative(source)
        next_number = sum(
            item.get("source_path") == source_relative for item in versions
        ) + 1
        version_id = str(uuid4())
        parent = next(
            (
                item["version_id"]
                for item in reversed(versions)
                if item.get("sha256") == base_sha256
            ),
            None,
        )
        version_path = family_dir / f"v{next_number:04d}_{safe_label}{source.suffix.lower()}"
        with version_path.open("x", encoding="utf-8") as stream:
            stream.write(proposed)
        created_at = datetime.now(UTC).isoformat()
        versions.append(
            {
                "version_id": version_id,
                "parent_id": parent,
                "source_path": source_relative,
                "version_path": self.relative(version_path),
                "sha256": proposed_sha256,
                "created_at": created_at,
                "session_id": str(context.session_id),
                "turn_id": str(context.turn_id),
                "label": safe_label,
            }
        )
        self._save_manifest(versions)
        return {
            "version_id": version_id,
            "parent_id": parent,
            "version_number": next_number,
            "version_path": self.relative(version_path),
            "sha256": proposed_sha256,
            "created_at": created_at,
            "source_path": self.relative(source),
        }

    def _resolve_path(self, value: str) -> Path:
        raw = Path(value)
        path = raw.resolve() if raw.is_absolute() else (self.storage_root / raw).resolve()
        try:
            path.relative_to(self.storage_root)
        except ValueError as exc:
            raise ResumeToolFailure(
                "path_not_allowed", "只能访问已配置简历目录内的文件"
            ) from exc
        return path

    def _load_manifest(self) -> list[dict[str, Any]]:
        if not self.manifest_path.exists():
            return []
        value = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if not isinstance(value, list):
            raise ResumeToolFailure("manifest_corrupted", "简历版本索引格式不正确")
        return [item for item in value if isinstance(item, dict)]

    def _save_manifest(self, versions: list[dict[str, Any]]) -> None:
        self.storage_root.mkdir(parents=True, exist_ok=True)
        temporary = self.manifest_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(versions, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.manifest_path)


class ResumeToolFailure(Exception):
    def __init__(self, code: str, display: str) -> None:
        super().__init__(display)
        self.code = code
        self.display = display


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sections_from_markdown(text: str) -> list[dict[str, str]]:
    sections: list[dict[str, str]] = []
    title = "正文"
    content: list[str] = []
    for line in text.splitlines():
        if line.startswith("#"):
            if content or sections:
                sections.append({"title": title, "content": "\n".join(content).strip()})
            title = line.lstrip("#").strip() or "未命名章节"
            content = []
        else:
            content.append(line)
    sections.append({"title": title, "content": "\n".join(content).strip()})
    return [item for item in sections if item["title"] or item["content"]]


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def _contains_alias(text: str, alias: str) -> bool:
    normalized_alias = _normalized_text(alias)
    if re.search(r"[\u4e00-\u9fff]", normalized_alias):
        return normalized_alias in text
    return re.search(
        rf"(?<![a-z0-9]){re.escape(normalized_alias)}(?![a-z0-9])",
        text,
    ) is not None


def _concepts_in(value: str) -> set[str]:
    normalized = _normalized_text(value)
    return {
        concept
        for concept, aliases in MATCH_CONCEPTS.items()
        if any(_contains_alias(normalized, alias) for alias in aliases)
    }


def _lexical_terms(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9+#.-]{2,}|[\u4e00-\u9fff]{2,}", value.casefold())
        if token not in {"and", "the", "with", "from", "using", "参与", "能够", "以及"}
    }


def _resume_evidence(text: str) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for section in sections_from_markdown(text):
        for raw_line in section["content"].splitlines():
            line = re.sub(r"^\s*[-*+]\s*", "", raw_line).strip()
            if not line:
                continue
            evidence.append(
                {
                    "section": section["title"],
                    "text": line,
                    "concepts": sorted(_concepts_in(line)),
                }
            )
    return evidence


def _job_criteria(payload: dict[str, Any]) -> list[dict[str, str]]:
    criteria: list[dict[str, str]] = []
    for kind, key in (
        ("responsibility", "responsibilities"),
        ("requirement", "requirements"),
        ("nice_to_have", "nice_to_have"),
    ):
        values = payload.get(key, [])
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            continue
        for value in values:
            text = str(value).strip()
            if text:
                criteria.append({"kind": kind, "text": text})
    return criteria


def _job_from_description(description: str, target_role: str) -> dict[str, Any]:
    value = description.strip()
    if not value:
        raise ResumeToolFailure("missing_job_description", "请选择具体岗位或提供完整 JD")
    if len(value) > MAX_JOB_DESCRIPTION_CHARS:
        raise ResumeToolFailure("job_description_too_large", "JD 内容过长，无法安全处理")
    requirement_markers = re.compile(
        r"熟悉|经验|负责|要求|参与|具备|掌握|build|develop|experience|"
        r"required|responsib|proficient|knowledge",
        re.IGNORECASE,
    )
    if len(value) < 20 and not requirement_markers.search(value):
        raise ResumeToolFailure(
            "invalid_job_description",
            "当前内容更像岗位名称而不是完整 JD；请提供岗位职责或任职要求",
        )
    lines = [
        re.sub(r"^\s*(?:[-*+]|\d+[.)、])\s*", "", line).strip()
        for line in value.splitlines()
    ]
    lines = [line for line in lines if line]
    return {
        "id": None,
        "title": target_role.strip() or "用户提供的岗位",
        "source": "user_provided",
        "requirements": lines or [value],
        "responsibilities": [],
        "nice_to_have": [],
    }


def compare_resume_with_job(
    resume_text: str,
    job: dict[str, Any],
) -> dict[str, Any]:
    evidence = _resume_evidence(resume_text)
    criteria = _job_criteria(job)
    if not criteria:
        raise ResumeToolFailure("invalid_job_description", "JD 中没有可比较的职责或要求")

    comparisons: list[dict[str, Any]] = []
    for criterion in criteria:
        criterion_concepts = _concepts_in(criterion["text"])
        criterion_terms = _lexical_terms(criterion["text"])
        ranked: list[tuple[float, dict[str, Any], set[str]]] = []
        for item in evidence:
            shared_concepts = criterion_concepts & set(item["concepts"])
            item_terms = _lexical_terms(item["text"])
            shared_terms = criterion_terms & item_terms
            lexical_ratio = len(shared_terms) / max(1, len(criterion_terms))
            rank = len(shared_concepts) * 10 + lexical_ratio
            if rank > 0:
                ranked.append((rank, item, shared_concepts))
        ranked.sort(key=lambda candidate: candidate[0], reverse=True)

        matched_concepts = set().union(
            *(candidate[2] for candidate in ranked[:3])
        ) if ranked else set()
        coverage = (
            len(matched_concepts) / len(criterion_concepts)
            if criterion_concepts
            else (min(1.0, ranked[0][0]) if ranked else 0.0)
        )
        if coverage >= 0.75:
            status = "matched"
        elif coverage > 0:
            status = "partial"
        else:
            status = "gap"
        comparisons.append(
            {
                "criterion_type": criterion["kind"],
                "criterion": criterion["text"],
                "status": status,
                "coverage": round(coverage, 3),
                "matched_concepts": sorted(matched_concepts),
                "missing_concepts": sorted(criterion_concepts - matched_concepts),
                "evidence": [
                    {
                        "section": candidate[1]["section"],
                        "text": candidate[1]["text"],
                        "matched_concepts": sorted(candidate[2]),
                    }
                    for candidate in ranked[:3]
                ],
            }
        )

    counts = {
        status: sum(item["status"] == status for item in comparisons)
        for status in ("matched", "partial", "gap")
    }
    required = [
        item for item in comparisons if item["criterion_type"] != "nice_to_have"
    ]
    weighted = sum(
        1.0 if item["status"] == "matched" else 0.5 if item["status"] == "partial" else 0.0
        for item in required
    )
    score = round(100 * weighted / max(1, len(required)))
    warnings = job.get("classroom_use", {}).get("risk_warnings", [])
    if not isinstance(warnings, list):
        warnings = []
    return {
        "method": "deterministic_concept_evidence_v1",
        "coverage_score": score,
        "requires_human_review": True,
        "summary": {**counts, "criteria_total": len(comparisons)},
        "comparisons": comparisons,
        "gaps": [
            {
                "criterion_type": item["criterion_type"],
                "criterion": item["criterion"],
                "status": item["status"],
                "missing_concepts": item["missing_concepts"],
            }
            for item in comparisons
            if item["status"] != "matched"
        ],
        "risk_warnings": [str(item) for item in warnings],
    }


def unified_diff(
    before: str, after: str, before_name: str, after_name: str
) -> tuple[str, bool]:
    value = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=before_name,
            tofile=after_name,
        )
    )
    if len(value) <= MAX_DIFF_CHARS:
        return value, False
    return value[:MAX_DIFF_CHARS] + "\n... diff 已截断 ...\n", True


class ResumeTool(Tool):
    def __init__(self, manager: ResumeManager) -> None:
        self.manager = manager

    async def _failure(self, exc: ResumeToolFailure) -> ToolResult:
        return ToolResult(ok=False, error_code=exc.code, display=exc.display)


class ReadResumeTool(ResumeTool):
    name = "read_resume"
    description = (
        "Read a Markdown or text resume from the configured resume directory. "
        "The path may be a base file such as resume.md or a saved version_path "
        "returned by list_resume_versions/save_resume_version."
    )
    risk_level = "read"
    input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "Path relative to the resume root, including versions/... paths."
                ),
            }
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        try:
            path, text, digest = self.manager.read(str(arguments.get("path", "")))
        except (ResumeToolFailure, UnicodeError) as exc:
            failure = exc if isinstance(exc, ResumeToolFailure) else ResumeToolFailure("parse_failed", "简历不是有效的 UTF-8 文本")
            return await self._failure(failure)
        return ToolResult(
            ok=True,
            data={
                "path": self.manager.relative(path),
                "sha256": digest,
                "format": path.suffix.lower().lstrip("."),
                "sections": sections_from_markdown(text),
            },
            display=f"已读取简历，共 {len(text)} 个字符",
            metadata={"path": self.manager.relative(path), "sha256": digest},
        )


class ListResumeVersionsTool(ResumeTool):
    name = "list_resume_versions"
    description = (
        "List resume files and saved versions in the configured resume directory. "
        "Use this before read_resume when the user does not know a path."
    )
    risk_level = "read"
    input_schema = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    async def execute(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        try:
            data = self.manager.list_resumes()
        except (ResumeToolFailure, OSError, json.JSONDecodeError) as exc:
            failure = (
                exc
                if isinstance(exc, ResumeToolFailure)
                else ResumeToolFailure(
                    "list_failed", "读取简历目录或版本索引失败"
                )
            )
            return await self._failure(failure)
        total = len(data["files"]) + len(data["versions"])
        return ToolResult(
            ok=True,
            data=data,
            display=f"找到 {total} 个简历文件或版本",
            metadata={"root": data["root"], "result_count": total},
        )


class SaveResumeTool(ResumeTool):
    name = "save_resume"
    description = (
        "Save a user-confirmed initial Markdown or text resume in the configured "
        "resume directory. Never overwrites an existing file."
    )
    risk_level = "write"
    input_schema = {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "minLength": 4,
                "maxLength": 120,
                "description": "A filename such as resume.md; directories are not allowed.",
            },
            "content": {
                "type": "string",
                "minLength": 1,
                "maxLength": MAX_RESUME_CHARS,
            },
            "confirmed": {"type": "boolean", "const": True},
        },
        "required": ["filename", "content", "confirmed"],
        "additionalProperties": False,
    }

    async def execute(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        try:
            data = self.manager.save_resume(
                str(arguments.get("filename", "")),
                str(arguments.get("content", "")),
                arguments.get("confirmed") is True,
            )
        except (ResumeToolFailure, OSError) as exc:
            failure = (
                exc
                if isinstance(exc, ResumeToolFailure)
                else ResumeToolFailure("save_failed", "保存简历文件失败")
            )
            return await self._failure(failure)
        return ToolResult(
            ok=True,
            data=data,
            display=f"简历已保存到 {data['path']}",
            metadata={"path": data["path"], "sha256": data["sha256"]},
        )


class CompareResumeTool(ResumeTool):
    name = "compare_resume"
    description = "Compare two resume files and return a unified diff without changing either file."
    risk_level = "read"
    input_schema = {
        "type": "object",
        "properties": {
            "base_path": {"type": "string", "minLength": 1},
            "target_path": {"type": "string", "minLength": 1},
        },
        "required": ["base_path", "target_path"],
        "additionalProperties": False,
    }

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        try:
            base_path, base, base_hash = self.manager.read(str(arguments.get("base_path", "")))
            target_path, target, target_hash = self.manager.read(str(arguments.get("target_path", "")))
        except ResumeToolFailure as exc:
            return await self._failure(exc)
        diff, truncated = unified_diff(
            base, target, self.manager.relative(base_path), self.manager.relative(target_path)
        )
        return ToolResult(
            ok=True,
            data={
                "base_path": self.manager.relative(base_path),
                "target_path": self.manager.relative(target_path),
                "base_sha256": base_hash,
                "target_sha256": target_hash,
                "changed": base_hash != target_hash,
                "diff": diff,
                "diff_truncated": truncated,
            },
            display="简历存在差异" if base_hash != target_hash else "两个简历版本内容一致",
            metadata={"base_sha256": base_hash, "target_sha256": target_hash},
        )


class CompareResumeToJdTool(ResumeTool):
    name = "compare_resume_to_jd"
    description = (
        "Compare one resume with one specific job description and return a "
        "traceable requirement-to-evidence matrix. Use this for job fit analysis; "
        "compare_resume is only for diffing two resume versions."
    )
    risk_level = "read"
    input_schema = {
        "type": "object",
        "properties": {
            "resume_path": {
                "type": "string",
                "minLength": 1,
                "description": "Path relative to the configured resume root.",
            },
            "job_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 80,
                "description": "ID of a JSON job record in the configured jobs directory.",
            },
            "job_description": {
                "type": "string",
                "minLength": 1,
                "maxLength": MAX_JOB_DESCRIPTION_CHARS,
                "description": "A complete user-provided JD. Do not use a broad role name.",
            },
            "target_role": {
                "type": "string",
                "maxLength": 200,
                "description": "Optional title for a user-provided JD.",
            },
        },
        "required": ["resume_path"],
        "oneOf": [
            {"required": ["job_id"]},
            {"required": ["job_description"]},
        ],
        "additionalProperties": False,
    }

    async def execute(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        job_id = str(arguments.get("job_id", "")).strip()
        job_description = str(arguments.get("job_description", "")).strip()
        if bool(job_id) == bool(job_description):
            return await self._failure(
                ResumeToolFailure(
                    "ambiguous_job_source" if job_id else "missing_job_description",
                    "请只选择一个具体岗位：提供 job_id 或完整 JD",
                )
            )
        try:
            resume_path, resume_text, resume_sha256 = self.manager.read(
                str(arguments.get("resume_path", ""))
            )
            if job_id:
                job_path, job, job_fingerprint = self.manager.read_job(job_id)
                job_source = {
                    "type": "job_record",
                    "job_id": job_id,
                    "path": job_path.resolve().relative_to(
                        self.manager.storage_root
                    ).as_posix(),
                    "source": job.get("source"),
                    "source_urls": job.get("source_urls", {}),
                }
            else:
                job = _job_from_description(
                    job_description, str(arguments.get("target_role", ""))
                )
                job_fingerprint = sha256_text(job_description)
                job_source = {"type": "user_provided", "job_id": None}
            comparison = compare_resume_with_job(resume_text, job)
        except (ResumeToolFailure, UnicodeError, OSError) as exc:
            failure = (
                exc
                if isinstance(exc, ResumeToolFailure)
                else ResumeToolFailure("comparison_failed", "读取或比较简历与 JD 失败")
            )
            return await self._failure(failure)

        data = {
            "resume": {
                "path": self.manager.relative(resume_path),
                "sha256": resume_sha256,
            },
            "job": {
                "id": job.get("id"),
                "title": job.get("title"),
                "company": job.get("company"),
                "location": job.get("location"),
                "fingerprint": job_fingerprint,
                "source": job_source,
            },
            **comparison,
        }
        summary = comparison["summary"]
        return ToolResult(
            ok=True,
            data=data,
            display=(
                f"已对比简历与 {job.get('title') or '指定岗位'}："
                f"匹配 {summary['matched']}，部分匹配 {summary['partial']}，"
                f"缺口 {summary['gap']}"
            ),
            metadata={
                "resume_path": self.manager.relative(resume_path),
                "resume_sha256": resume_sha256,
                "job_id": job.get("id"),
                "job_fingerprint": job_fingerprint,
                "coverage_score": comparison["coverage_score"],
                "criteria_total": summary["criteria_total"],
            },
        )


class DraftResumePatchTool(ResumeTool):
    name = "draft_resume_patch"
    description = (
        "Create an evidence-backed resume patch draft without overwriting the source file. "
        "For JD-targeted changes, call compare_resume_to_jd first and use only its "
        "verbatim resume evidence; never convert a reported gap into invented experience."
    )
    risk_level = "write"
    input_schema = {
        "type": "object",
        "properties": {
            "resume_path": {"type": "string", "minLength": 1},
            "target_text": {"type": "string", "minLength": 1},
            "replacement_text": {"type": "string", "minLength": 1},
            "evidence": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "minItems": 1,
            },
        },
        "required": ["resume_path", "target_text", "replacement_text", "evidence"],
        "additionalProperties": False,
    }

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        try:
            data = self.manager.create_draft(
                str(arguments.get("resume_path", "")),
                str(arguments.get("target_text", "")),
                str(arguments.get("replacement_text", "")),
                [str(item) for item in arguments.get("evidence", [])],
            )
        except ResumeToolFailure as exc:
            return await self._failure(exc)
        return ToolResult(
            ok=True,
            data=data,
            display="已生成简历补丁草稿，保存新版本前需要用户确认",
            metadata={
                "draft_id": data["draft_id"],
                "base_sha256": data["base_sha256"],
            },
        )


class SaveResumeVersionTool(ResumeTool):
    name = "save_resume_version"
    description = "Save a confirmed resume draft as a new immutable version without overwriting the source."
    risk_level = "write"
    input_schema = {
        "type": "object",
        "properties": {
            "draft_id": {"type": "string", "minLength": 36, "maxLength": 36},
            "base_sha256": {"type": "string", "minLength": 64, "maxLength": 64},
            "confirmed": {"type": "boolean", "const": True},
            "label": {"type": "string", "maxLength": 40, "default": "resume"},
        },
        "required": ["draft_id", "base_sha256", "confirmed"],
        "additionalProperties": False,
    }

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        try:
            data = self.manager.save_version(
                str(arguments.get("draft_id", "")),
                str(arguments.get("base_sha256", "")),
                arguments.get("confirmed") is True,
                str(arguments.get("label", "resume")),
                context,
            )
        except (ResumeToolFailure, OSError, json.JSONDecodeError) as exc:
            failure = exc if isinstance(exc, ResumeToolFailure) else ResumeToolFailure("save_failed", "保存简历版本失败")
            return await self._failure(failure)
        return ToolResult(
            ok=True,
            data=data,
            display=f"已保存简历版本 v{data['version_number']:04d}",
            metadata={
                "version_id": data["version_id"],
                "sha256": data["sha256"],
            },
        )
