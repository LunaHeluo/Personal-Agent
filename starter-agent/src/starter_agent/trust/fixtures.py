from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any

import yaml

from starter_agent.capabilities.models import canonical_json_sha256
from starter_agent.mcp.config import contains_high_confidence_secret
from starter_agent.trust.models import EvalFixture


class FixtureLoadError(RuntimeError):
    pass


_SECRET_KEY = re.compile(
    r"(?:api[_-]?key|authorization|cookie|credential|pass(?:word|wd)?|secret|token)",
    flags=re.IGNORECASE,
)
_SECRET_TEXT = re.compile(
    r"(?i)(?:bearer\s+\S+|(?:api[_-]?key|authorization|cookie|password|secret|token)\s*[=:]\s*\S+)"
)

_REQUIRED_FIELDS_BY_TYPE: dict[str, tuple[str, ...]] = {
    "serpapi_search": ("query", "results"),
    "jd_page": ("source_url", "title", "company", "body_text"),
    "resume_chunks": ("knowledge_base_id", "chunks"),
    "mcp_response": ("tool_name", "result"),
    "tool_error": ("tool_name", "error"),
    "policy": ("policy_id", "rules"),
    "injection": ("vectors",),
    "knowledge_routing": ("scenarios",),
}


@dataclass(frozen=True, slots=True)
class LoadedFixture:
    id: str
    fixture_type: str
    version: str
    path: Path
    expected_hash: str
    content_hash: str
    data: dict[str, Any]
    record: EvalFixture


@dataclass(frozen=True, slots=True)
class FixtureManifest:
    id: str
    version: str
    manifest_hash: str
    fixtures: tuple[LoadedFixture, ...]

    def by_id(self, fixture_id: str) -> LoadedFixture:
        for fixture in self.fixtures:
            if fixture.id == fixture_id:
                return fixture
        raise KeyError(fixture_id)


class JobResearchFixtureLoader:
    def __init__(self, fixture_root: Path | str) -> None:
        self.fixture_root = Path(fixture_root).resolve()
        self.manifest_path = self.fixture_root / "manifest.yaml"

    def load_manifest(self) -> FixtureManifest:
        if not self.manifest_path.is_file():
            raise FixtureLoadError(f"fixture manifest not found: {self.manifest_path}")
        manifest_text = self.manifest_path.read_text(encoding="utf-8")
        self._reject_secret_text(manifest_text, source=str(self.manifest_path))
        manifest_hash = hashlib.sha256(manifest_text.encode("utf-8")).hexdigest()
        raw = yaml.safe_load(manifest_text) or {}
        if not isinstance(raw, dict):
            raise FixtureLoadError("fixture manifest must be an object")
        fixtures_raw = raw.get("fixtures")
        if not isinstance(fixtures_raw, list):
            raise FixtureLoadError("fixture manifest must contain fixtures list")
        fixtures = tuple(self._load_fixture(item) for item in fixtures_raw)
        return FixtureManifest(
            id=self._required_text(raw, "id"),
            version=self._required_text(raw, "version"),
            manifest_hash=manifest_hash,
            fixtures=fixtures,
        )

    def _load_fixture(self, item: Any) -> LoadedFixture:
        if not isinstance(item, dict):
            raise FixtureLoadError("fixture entry must be an object")
        fixture_id = self._required_text(item, "id")
        fixture_type = self._required_text(item, "type")
        version = self._required_text(item, "version")
        case_ids_raw = item.get("case_ids", [])
        if not isinstance(case_ids_raw, list) or not all(
            isinstance(case_id, str) for case_id in case_ids_raw
        ):
            raise FixtureLoadError(f"fixture {fixture_id} case_ids must be strings")
        relative = self._required_text(item, "path")
        path = (self.fixture_root / relative).resolve()
        try:
            path.relative_to(self.fixture_root)
        except ValueError as exc:
            raise FixtureLoadError(f"fixture path outside fixture root: {relative}") from exc
        if not path.is_file():
            raise FixtureLoadError(f"fixture file not found: {relative}")
        content = path.read_bytes()
        content_hash = hashlib.sha256(content).hexdigest()
        text = content.decode("utf-8")
        self._reject_secret_text(text, source=relative)
        data = self._load_data(path, text)
        self._validate_fixture_data(fixture_id, fixture_type, data)
        self._reject_secret_value(data, source=relative)
        expected_hash = self._required_hash(item, "sha256")
        if content_hash != expected_hash:
            raise FixtureLoadError(f"fixture hash mismatch: {fixture_id}")
        redaction = item.get("redaction")
        if not isinstance(redaction, dict) or not redaction:
            raise FixtureLoadError(f"fixture {fixture_id} must document redaction")
        record = EvalFixture(
            id=fixture_id,
            fixture_type=fixture_type,
            version=version,
            manifest_hash=canonical_json_sha256(
                {
                    "manifest": self.manifest_path.name,
                    "fixture_id": fixture_id,
                    "version": version,
                }
            ),
            content_hash=content_hash,
            source_ref=f"fixture:{relative}",
            summary=self._summary_for(fixture_type, data),
            redaction_summary=redaction,
        )
        return LoadedFixture(
            id=fixture_id,
            fixture_type=fixture_type,
            version=version,
            path=path,
            expected_hash=expected_hash,
            content_hash=content_hash,
            data=data,
            record=record,
        )

    def _load_data(self, path: Path, text: str) -> dict[str, Any]:
        if path.suffix.lower() in {".yaml", ".yml"}:
            value = yaml.safe_load(text) or {}
        elif path.suffix.lower() == ".json":
            value = json.loads(text)
        else:
            raise FixtureLoadError(f"unsupported fixture file type: {path.suffix}")
        if not isinstance(value, dict):
            raise FixtureLoadError("fixture data must be an object")
        return value

    def _validate_fixture_data(
        self,
        fixture_id: str,
        fixture_type: str,
        data: dict[str, Any],
    ) -> None:
        required = _REQUIRED_FIELDS_BY_TYPE.get(fixture_type)
        if required is None:
            raise FixtureLoadError(f"unsupported fixture type: {fixture_type}")
        missing = [field for field in required if field not in data]
        if missing:
            raise FixtureLoadError(
                f"fixture {fixture_id} missing required fields: {', '.join(missing)}"
            )

    def _summary_for(self, fixture_type: str, data: dict[str, Any]) -> dict[str, Any]:
        if fixture_type == "serpapi_search":
            return {
                "query": data.get("query"),
                "result_count": len(data.get("results", [])),
            }
        if fixture_type == "jd_page":
            return {
                "source_url": data.get("source_url"),
                "title": data.get("title"),
                "company": data.get("company"),
            }
        if fixture_type == "resume_chunks":
            return {
                "knowledge_base_id": data.get("knowledge_base_id"),
                "chunk_count": len(data.get("chunks", [])),
            }
        if fixture_type == "mcp_response":
            return {"tool_name": data.get("tool_name"), "status": data.get("status")}
        if fixture_type == "tool_error":
            error = data.get("error")
            code = error.get("code") if isinstance(error, dict) else None
            return {"tool_name": data.get("tool_name"), "error_code": code}
        if fixture_type == "policy":
            return {
                "policy_id": data.get("policy_id"),
                "rule_count": len(data.get("rules", [])),
            }
        if fixture_type == "injection":
            return {"vector_count": len(data.get("vectors", []))}
        if fixture_type == "knowledge_routing":
            return {"scenario_count": len(data.get("scenarios", []))}
        return {"fixture_type": fixture_type}

    def _required_text(self, value: dict[str, Any], key: str) -> str:
        item = value.get(key)
        if not isinstance(item, str) or not item:
            raise FixtureLoadError(f"fixture manifest field is required: {key}")
        return item

    def _required_hash(self, value: dict[str, Any], key: str) -> str:
        item = self._required_text(value, key)
        if not re.fullmatch(r"[0-9a-f]{64}", item):
            raise FixtureLoadError(f"fixture manifest field must be sha256: {key}")
        return item

    def _reject_secret_text(self, value: str, *, source: str) -> None:
        if _SECRET_TEXT.search(value) or contains_high_confidence_secret(value):
            raise FixtureLoadError(f"fixture contains unredacted secret text: {source}")

    def _reject_secret_value(self, value: Any, *, source: str, sensitive: bool = False) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                self._reject_secret_value(
                    item,
                    source=source,
                    sensitive=sensitive or bool(_SECRET_KEY.search(str(key))),
                )
            return
        if isinstance(value, list):
            for item in value:
                self._reject_secret_value(item, source=source, sensitive=sensitive)
            return
        if isinstance(value, str):
            if sensitive and value.casefold() not in {
                "***",
                "<redacted>",
                "[redacted]",
                "redacted",
                "none",
                "absent",
            }:
                raise FixtureLoadError(f"fixture contains unredacted secret: {source}")
            self._reject_secret_text(value, source=source)
