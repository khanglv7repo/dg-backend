from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.core.errors import ConfigurationError
from app.schemas.classification import (
    ClassificationResult,
    MatchOutcome,
    TagSuggestion,
)
from app.schemas.events import MetadataEventRequest, MetadataField


@dataclass(frozen=True, slots=True)
class RuleMatch:
    rule_id: str
    tag: str
    confidence: float
    rationale: str
    field_path: str | None
    auto_apply: bool


class ClassificationRuleEngine:
    def __init__(self, document: dict[str, Any]) -> None:
        self.document = document
        canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
        self.configuration_version = hashlib.sha256(canonical.encode()).hexdigest()[:16]
        self.rules = document.get("rules", [])
        if not isinstance(self.rules, list):
            raise ConfigurationError("classification rules must be a list")

    @classmethod
    def from_path(cls, path: Path) -> "ClassificationRuleEngine":
        if not path.exists():
            raise ConfigurationError(f"classification rules file not found: {path}")
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(document)

    def evaluate(self, event: MetadataEventRequest) -> ClassificationResult:
        matches: list[RuleMatch] = []
        matches.extend(self._evaluate_target(event, None))
        for field in event.fields:
            matches.extend(self._evaluate_target(event, field))

        combined: dict[tuple[str, str | None], RuleMatch] = {}
        for match in matches:
            key = (match.tag, match.field_path)
            existing = combined.get(key)
            if not existing:
                combined[key] = match
                continue
            combined[key] = RuleMatch(
                rule_id=",".join(sorted(set(existing.rule_id.split(",") + [match.rule_id]))),
                tag=match.tag,
                confidence=max(existing.confidence, match.confidence),
                rationale=f"{existing.rationale} | {match.rationale}",
                field_path=match.field_path,
                auto_apply=existing.auto_apply and match.auto_apply,
            )
        matches = list(combined.values())
        if not matches:
            return ClassificationResult(
                outcome=MatchOutcome.NO_MATCH,
                rule_version=self.configuration_version,
                evidence={"evaluated_rules": len(self.rules)},
            )

        by_target: dict[str, set[str]] = {}
        for match in matches:
            by_target.setdefault(match.field_path or "$entity", set()).add(match.tag)
        ambiguous = any(len(tags) > 1 for tags in by_target.values())
        suggestions = [
            TagSuggestion(
                tag=match.tag,
                confidence=match.confidence,
                rationale=match.rationale,
                rule_id=match.rule_id,
                field_path=match.field_path,
            )
            for match in sorted(matches, key=lambda item: (item.field_path or "", item.tag))
        ]
        trusted = not ambiguous and all(match.auto_apply for match in matches)
        return ClassificationResult(
            outcome=MatchOutcome.AMBIGUOUS if ambiguous else MatchOutcome.EXACT,
            suggestions=suggestions,
            rule_version=self.configuration_version,
            trusted_auto_apply=trusted,
            evidence={"matched_rules": sorted({m.rule_id for m in matches})},
        )

    def _evaluate_target(
        self, event: MetadataEventRequest, field: MetadataField | None
    ) -> list[RuleMatch]:
        target = "column" if field else "entity"
        name = field.name if field else event.entity_name
        description = field.description if field else event.description
        data_type = field.data_type if field else None
        result: list[RuleMatch] = []
        for rule in self.rules:
            if rule.get("target", "column") != target:
                continue
            if self._matches(rule.get("when", {}), name, description, data_type):
                field_path = f"columns.{field.name}" if field else None
                result.append(
                    RuleMatch(
                        rule_id=str(rule["id"]),
                        tag=str(rule["tag"]),
                        confidence=float(rule.get("confidence", 1.0)),
                        rationale=str(rule.get("rationale", f"Matched rule {rule['id']}")),
                        field_path=field_path,
                        auto_apply=bool(rule.get("auto_apply", False)),
                    )
                )
        return result

    @staticmethod
    def _matches(
        condition: dict[str, Any],
        name: str,
        description: str | None,
        data_type: str | None,
    ) -> bool:
        name_lower = name.lower()
        description_lower = (description or "").lower()
        exact_names = {str(value).lower() for value in condition.get("name_exact", [])}
        if exact_names and name_lower not in exact_names:
            return False
        name_regex = condition.get("name_regex")
        if name_regex and not re.search(str(name_regex), name, re.IGNORECASE):
            return False
        description_regex = condition.get("description_regex")
        if description_regex and not re.search(
            str(description_regex), description or "", re.IGNORECASE
        ):
            return False
        allowed_types = {str(value).lower() for value in condition.get("data_types", [])}
        if allowed_types and (data_type or "").lower() not in allowed_types:
            return False
        contains_any = [str(value).lower() for value in condition.get("contains_any", [])]
        if contains_any and not any(
            token in name_lower or token in description_lower for token in contains_any
        ):
            return False
        return bool(condition)
