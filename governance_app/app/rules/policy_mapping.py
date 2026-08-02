
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from app.core.errors import ConfigurationError
from app.schemas.policy import DesiredPolicy, VerificationCase


class PolicyMappingResolver:
    def __init__(self, document: dict[str, Any]) -> None:
        self.document = document
        canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
        self.configuration_version = hashlib.sha256(canonical.encode()).hexdigest()[:16]
        self.mappings = document.get("mappings", [])
        if not isinstance(self.mappings, list):
            raise ConfigurationError("policy mappings must be a list")

    @classmethod
    def from_path(cls, path: Path) -> "PolicyMappingResolver":
        if not path.exists():
            raise ConfigurationError(f"policy mapping file not found: {path}")
        return cls(yaml.safe_load(path.read_text(encoding="utf-8")) or {})

    def resolve_all(
        self,
        *,
        tags: list[str],
        entity_fqn: str,
        field_paths: dict[str, list[str]],
        service: str,
    ) -> list[DesiredPolicy]:
        resolved: list[DesiredPolicy] = []
        for tag in sorted(set(tags)):
            candidates = [mapping for mapping in self.mappings if mapping.get("tag") == tag]
            if not candidates:
                continue
            if len(candidates) != 1:
                raise ConfigurationError(
                    f"tag {tag!r} must resolve to exactly one policy mapping; got {len(candidates)}"
                )
            mapping = candidates[0]
            targets = sorted(set(field_paths.get(tag) or ["*"]))
            for selected_field_path in targets:
                selected_field_name = (
                    selected_field_path.split(".", 1)[1]
                    if selected_field_path.startswith("columns.")
                    else selected_field_path
                )
                replacements = {
                    "${entity_fqn}": entity_fqn,
                    "${field_path}": selected_field_path,
                    "${field_name}": selected_field_name,
                }
                resources = {
                    key: [self._render(str(value), replacements) for value in values]
                    for key, values in mapping.get("resources", {}).items()
                }
                policy_key = self._render(str(mapping["policy_key"]), replacements)
                policy_name = self._render(str(mapping.get("name", policy_key)), replacements)
                verifications: list[VerificationCase] = []
                for item in mapping.get("verification_cases", []):
                    rendered = dict(item)
                    rendered["sql"] = self._render(str(rendered.get("sql", "")), replacements)
                    verifications.append(VerificationCase.model_validate(rendered))
                resolved.append(
                    DesiredPolicy(
                        policy_key=policy_key,
                        name=policy_name,
                        description=str(mapping.get("description", f"Policy generated for {tag}")),
                        service=service,
                        resources=resources,
                        users=[str(value) for value in mapping.get("users", [])],
                        groups=[str(value) for value in mapping.get("groups", [])],
                        accesses=[str(value) for value in mapping.get("accesses", ["select"])],
                        deny=bool(mapping.get("deny", False)),
                        verification_cases=verifications,
                        source_version=self.configuration_version,
                    )
                )
        return resolved

    @staticmethod
    def _render(template: str, replacements: dict[str, str]) -> str:
        result = template
        for token, value in replacements.items():
            result = result.replace(token, value)
        return result
