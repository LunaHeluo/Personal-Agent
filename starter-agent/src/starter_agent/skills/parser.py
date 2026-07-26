from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

from starter_agent.skills.models import SkillDefinition, SkillDependency


class SkillParseError(ValueError):
    pass


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate keys at every mapping depth."""


def _construct_unique_mapping(
    loader: yaml.SafeLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"duplicate key: {key}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


class SkillParser:
    """Parse a bounded YAML-frontmatter SKILL.md into a typed definition."""

    def parse_file(self, path: Path) -> SkillDefinition:
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise SkillParseError(f"skill_read_failed:{path.name}") from exc
        return self.parse(raw, source_path=path.as_posix())

    def parse(self, raw: str, *, source_path: str) -> SkillDefinition:
        if len(raw) > 200_000 or not raw.startswith("---\n"):
            raise SkillParseError("skill_frontmatter_required")
        closing = raw.find("\n---\n", 4)
        if closing < 0:
            raise SkillParseError("skill_frontmatter_unterminated")
        frontmatter_text = raw[4:closing]
        definition = raw[closing + 5 :].strip()
        if not definition:
            raise SkillParseError("skill_definition_required")
        try:
            metadata = yaml.load(frontmatter_text, Loader=_UniqueKeySafeLoader)
        except yaml.YAMLError as exc:
            error = (
                "skill_frontmatter_duplicate_key"
                if "duplicate key" in str(exc)
                else "skill_frontmatter_invalid"
            )
            raise SkillParseError(error) from exc
        if not isinstance(metadata, dict):
            raise SkillParseError("skill_frontmatter_invalid")
        try:
            project_metadata = metadata.get("metadata", {})
            if not isinstance(project_metadata, dict):
                raise SkillParseError("skill_metadata_invalid")

            def project_field(name: str, default: Any = None) -> Any:
                return metadata.get(name, project_metadata.get(name, default))

            dependencies = self._dependencies(
                project_field("dependencies")
            )
            return SkillDefinition(
                name=metadata["name"],
                description=metadata["description"],
                version=project_field("version"),
                source=project_field("source"),
                source_path=source_path,
                enabled=project_field("enabled", False),
                dependencies=dependencies,
                trigger_examples=self._strings(
                    project_field("trigger_examples"),
                    "trigger_examples",
                ),
                negative_examples=self._strings(
                    project_field("negative_examples"),
                    "negative_examples",
                ),
                validation=self._strings(
                    project_field("validation"),
                    "validation",
                ),
                failure_policy=self._strings(
                    project_field("failure_policy"),
                    "failure_policy",
                ),
                definition=raw,
                snapshot_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            )
        except (KeyError, TypeError, ValidationError) as exc:
            raise SkillParseError("skill_frontmatter_incomplete") from exc

    @staticmethod
    def _dependencies(value: Any) -> tuple[SkillDependency, ...]:
        if not isinstance(value, dict):
            raise SkillParseError("skill_dependencies_invalid")
        dependencies: list[SkillDependency] = []
        names = {
            "tools": "tool",
            "mcp": "mcp",
            "services": "service",
        }
        if set(value) - set(names):
            raise SkillParseError("skill_dependencies_invalid")
        for group, kind in names.items():
            items = value.get(group, [])
            if not isinstance(items, list):
                raise SkillParseError("skill_dependencies_invalid")
            for item in items:
                if not isinstance(item, str) or not item.strip():
                    raise SkillParseError("skill_dependencies_invalid")
                dependencies.append(
                    SkillDependency(kind=kind, name=item.strip())
                )
        if not dependencies:
            raise SkillParseError("skill_dependencies_required")
        keys = [item.key for item in dependencies]
        if len(keys) != len(set(keys)):
            raise SkillParseError("skill_dependency_duplicate")
        return tuple(dependencies)

    @staticmethod
    def _strings(value: Any, field: str) -> tuple[str, ...]:
        if (
            not isinstance(value, list)
            or not value
            or any(not isinstance(item, str) or not item.strip() for item in value)
        ):
            raise SkillParseError(f"skill_{field}_invalid")
        return tuple(item.strip() for item in value)
