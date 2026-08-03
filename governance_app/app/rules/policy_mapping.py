from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from app.core.errors import ConfigurationError
from app.schemas.policy import DesiredPolicy

_RUNTIME_TOKENS = ("${entity_fqn}", "${field_path}", "${field_name}")


class PolicyMappingResolver:
    """Resolve config/policies.yaml into static Ranger tag policies.

    v0.5 intentionally removes per-asset policy rendering. The same policy exists
    independently of OpenMetadata assets and is reconciled to the Ranger tag
    service at backend startup.
    """

    def __init__(self, document: dict[str, Any]) -> None:
        self.document = document
        canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
        self.configuration_version = hashlib.sha256(canonical.encode()).hexdigest()[:16]

        raw = document.get("tag_policies")
        if raw is None:
            raw = document.get("mappings", [])
        if not isinstance(raw, list):
            raise ConfigurationError("tag_policies must be a list")

        self.mappings = raw
        self._validate()

    @classmethod
    def from_path(cls, path: Path) -> "PolicyMappingResolver":
        if not path.exists():
            raise ConfigurationError(f"policy mapping file not found: {path}")
        return cls(yaml.safe_load(path.read_text(encoding="utf-8")) or {})

    @property
    def governed_tags(self) -> tuple[str, ...]:
        return tuple(sorted({str(item["tag"]) for item in self.mappings}))

    def resolve_all(
        self,
        *,
        service: str,
        tags: list[str] | None = None,
        **_legacy_runtime_arguments: Any,
    ) -> list[DesiredPolicy]:
        """Return static tag policies for the configured Ranger tag service.

        ``tags`` is optional and only filters the catalog. Legacy runtime keyword
        arguments are accepted so an in-flight local upgrade fails less abruptly,
        but entity/field values are deliberately not used for policy rendering.
        """

        selected = set(tags) if tags is not None else set(self.governed_tags)
        resolved: list[DesiredPolicy] = []

        for mapping in sorted(self.mappings, key=lambda item: str(item.get("tag", ""))):
            tag = str(mapping["tag"])
            if tag not in selected:
                continue

            policy_key = str(mapping.get("policy_key") or f"tag-policy:{tag}")
            name = str(mapping.get("name") or f"dg-tag-{_slug(tag)}")
            description = str(
                mapping.get(
                    "description",
                    f"Static Ranger tag policy generated for {tag}.",
                )
            )

            resolved.append(
                DesiredPolicy(
                    policy_key=policy_key,
                    name=name,
                    description=description,
                    service=service,
                    resources={"tag": [tag]},
                    users=[str(value) for value in mapping.get("users", [])],
                    groups=[str(value) for value in mapping.get("groups", [])],
                    accesses=[str(value) for value in mapping.get("accesses", ["select"])],
                    deny=bool(mapping.get("deny", False)),
                    verification_cases=[],
                    source_version=self.configuration_version,
                )
            )

        return resolved

    def _validate(self) -> None:
        seen_tags: set[str] = set()
        seen_names: set[str] = set()

        for index, mapping in enumerate(self.mappings):
            if not isinstance(mapping, dict):
                raise ConfigurationError(f"tag_policies[{index}] must be an object")

            tag = str(mapping.get("tag") or "").strip()
            if not tag:
                raise ConfigurationError(f"tag_policies[{index}].tag is required")
            if tag in seen_tags:
                raise ConfigurationError(f"duplicate Ranger tag policy mapping for {tag!r}")
            seen_tags.add(tag)

            serialized = json.dumps(mapping, sort_keys=True)
            forbidden = [token for token in _RUNTIME_TOKENS if token in serialized]
            if forbidden:
                raise ConfigurationError(
                    "config/policies.yaml is still using per-asset Ranger policy "
                    f"placeholders for tag {tag!r}: {', '.join(forbidden)}. "
                    "v0.5 policies must be static tag policies."
                )

            accesses = [
                str(value).strip()
                for value in mapping.get("accesses", ["trino:select"])
            ]
            invalid_accesses = [
                access
                for access in accesses
                if not access or ":" not in access
            ]
            if invalid_accesses:
                raise ConfigurationError(
                    "Ranger tag policies require namespaced access types; "
                    f"tag {tag!r} has invalid accesses {invalid_accesses!r}. "
                    "For Trino use values such as 'trino:select'."
                )

            name = str(mapping.get("name") or f"dg-tag-{_slug(tag)}")
            if name in seen_names:
                raise ConfigurationError(f"duplicate Ranger policy name {name!r}")
            seen_names.add(name)


def _slug(value: str) -> str:
    return "".join(char.lower() if char.isalnum() else "-" for char in value).strip("-")
