from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

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
    """Deterministic classification engine over a validated JSON document."""

    def __init__(self, document: dict[str, Any]) -> None:
        if not isinstance(document, dict):
            raise ConfigurationError(
                "classification rule document must be a JSON object"
            )

        self.document = document
        canonical = json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        self.configuration_sha256 = hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()
        self.configuration_version = self.configuration_sha256[:16]
        self.declared_version = (
            str(document["version"])
            if document.get("version") is not None
            else None
        )

        rules = document.get("rules")
        if not isinstance(rules, list) or not rules:
            raise ConfigurationError(
                "classification rules must be a non-empty JSON array"
            )

        self.rules = rules
        self._validate_rules()

    @classmethod
    def from_json_bytes(
        cls,
        payload: bytes,
    ) -> "ClassificationRuleEngine":
        try:
            document = json.loads(
                payload.decode("utf-8-sig")
            )
        except UnicodeDecodeError as exc:
            raise ConfigurationError(
                "classification rule file must be UTF-8 JSON"
            ) from exc
        except json.JSONDecodeError as exc:
            raise ConfigurationError(
                "invalid classification rule JSON",
                details={
                    "line": exc.lineno,
                    "column": exc.colno,
                    "message": exc.msg,
                },
            ) from exc

        if not isinstance(document, dict):
            raise ConfigurationError(
                "classification rule JSON root must be an object"
            )
        return cls(document)

    def _validate_rules(self) -> None:
        seen_ids: set[str] = set()

        for index, rule in enumerate(self.rules):
            if not isinstance(rule, dict):
                raise ConfigurationError(
                    f"classification rule at index {index} must be an object"
                )

            rule_id = str(rule.get("id") or "").strip()
            tag = str(rule.get("tag") or "").strip()
            target = str(rule.get("target", "column")).strip()
            condition = rule.get("when")

            if not rule_id:
                raise ConfigurationError(
                    f"classification rule at index {index} requires id"
                )
            if rule_id in seen_ids:
                raise ConfigurationError(
                    f"duplicate classification rule id {rule_id!r}"
                )
            seen_ids.add(rule_id)

            if not tag:
                raise ConfigurationError(
                    f"classification rule {rule_id!r} requires tag"
                )
            if target not in {"entity", "column"}:
                raise ConfigurationError(
                    f"classification rule {rule_id!r} target must be "
                    "'entity' or 'column'"
                )
            if not isinstance(condition, dict) or not condition:
                raise ConfigurationError(
                    f"classification rule {rule_id!r} requires non-empty when"
                )

            supported = {
                "name_exact",
                "name_regex",
                "description_regex",
                "data_types",
                "contains_any",
            }
            unknown = sorted(set(condition) - supported)
            if unknown:
                raise ConfigurationError(
                    f"classification rule {rule_id!r} has unsupported "
                    f"conditions: {', '.join(unknown)}"
                )

            for list_key in (
                "name_exact",
                "data_types",
                "contains_any",
            ):
                if (
                    list_key in condition
                    and not isinstance(condition[list_key], list)
                ):
                    raise ConfigurationError(
                        f"classification rule {rule_id!r} condition "
                        f"{list_key!r} must be an array"
                    )

            for regex_key in (
                "name_regex",
                "description_regex",
            ):
                pattern = condition.get(regex_key)
                if pattern is None:
                    continue
                if not isinstance(pattern, str) or not pattern:
                    raise ConfigurationError(
                        f"classification rule {rule_id!r} condition "
                        f"{regex_key!r} must be a non-empty string"
                    )
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise ConfigurationError(
                        f"classification rule {rule_id!r} has invalid "
                        f"{regex_key}: {exc}"
                    ) from exc

            confidence = rule.get("confidence", 1.0)
            if isinstance(confidence, bool):
                raise ConfigurationError(
                    f"classification rule {rule_id!r} confidence must "
                    "be a number between 0 and 1"
                )
            try:
                confidence_value = float(confidence)
            except (TypeError, ValueError) as exc:
                raise ConfigurationError(
                    f"classification rule {rule_id!r} confidence must "
                    "be a number between 0 and 1"
                ) from exc
            if not 0.0 <= confidence_value <= 1.0:
                raise ConfigurationError(
                    f"classification rule {rule_id!r} confidence must "
                    "be between 0 and 1"
                )

            if (
                "auto_apply" in rule
                and not isinstance(rule["auto_apply"], bool)
            ):
                raise ConfigurationError(
                    f"classification rule {rule_id!r} auto_apply must "
                    "be boolean"
                )

    def evaluate(
        self,
        event: MetadataEventRequest,
    ) -> ClassificationResult:
        matches: list[RuleMatch] = []
        matches.extend(self._evaluate_target(event, None))

        for field in event.fields:
            matches.extend(
                self._evaluate_target(event, field)
            )

        combined: dict[
            tuple[str, str | None],
            RuleMatch,
        ] = {}

        for match in matches:
            key = (match.tag, match.field_path)
            existing = combined.get(key)
            if not existing:
                combined[key] = match
                continue

            combined[key] = RuleMatch(
                rule_id=",".join(
                    sorted(
                        set(
                            existing.rule_id.split(",")
                            + [match.rule_id]
                        )
                    )
                ),
                tag=match.tag,
                confidence=max(
                    existing.confidence,
                    match.confidence,
                ),
                rationale=(
                    f"{existing.rationale} | {match.rationale}"
                ),
                field_path=match.field_path,
                auto_apply=(
                    existing.auto_apply
                    and match.auto_apply
                ),
            )

        matches = list(combined.values())

        if not matches:
            return ClassificationResult(
                outcome=MatchOutcome.NO_MATCH,
                rule_version=self.configuration_version,
                evidence={
                    "evaluated_rules": len(self.rules),
                    "rule_document_sha256":
                    self.configuration_sha256,
                    "declared_version":
                    self.declared_version,
                },
            )

        by_target: dict[str, set[str]] = {}
        for match in matches:
            by_target.setdefault(
                match.field_path or "$entity",
                set(),
            ).add(match.tag)

        ambiguous = any(
            len(tags) > 1
            for tags in by_target.values()
        )

        suggestions = [
            TagSuggestion(
                tag=match.tag,
                confidence=match.confidence,
                rationale=match.rationale,
                rule_id=match.rule_id,
                field_path=match.field_path,
            )
            for match in sorted(
                matches,
                key=lambda item: (
                    item.field_path or "",
                    item.tag,
                ),
            )
        ]

        trusted = (
            not ambiguous
            and all(
                match.auto_apply
                for match in matches
            )
        )

        return ClassificationResult(
            outcome=(
                MatchOutcome.AMBIGUOUS
                if ambiguous
                else MatchOutcome.EXACT
            ),
            suggestions=suggestions,
            rule_version=self.configuration_version,
            trusted_auto_apply=trusted,
            evidence={
                "matched_rules": sorted(
                    {m.rule_id for m in matches}
                ),
                "rule_document_sha256":
                self.configuration_sha256,
                "declared_version":
                self.declared_version,
            },
        )

    def _evaluate_target(
        self,
        event: MetadataEventRequest,
        field: MetadataField | None,
    ) -> list[RuleMatch]:
        target = "column" if field else "entity"
        name = (
            field.name
            if field
            else event.entity_name
        )
        description = (
            field.description
            if field
            else event.description
        )
        data_type = (
            field.data_type
            if field
            else None
        )
        result: list[RuleMatch] = []

        for rule in self.rules:
            if rule.get("target", "column") != target:
                continue

            if self._matches(
                rule.get("when", {}),
                name,
                description,
                data_type,
            ):
                field_path = (
                    f"columns.{field.name}"
                    if field
                    else None
                )
                result.append(
                    RuleMatch(
                        rule_id=str(rule["id"]),
                        tag=str(rule["tag"]),
                        confidence=float(
                            rule.get("confidence", 1.0)
                        ),
                        rationale=str(
                            rule.get(
                                "rationale",
                                f"Matched rule {rule['id']}",
                            )
                        ),
                        field_path=field_path,
                        auto_apply=bool(
                            rule.get(
                                "auto_apply",
                                False,
                            )
                        ),
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
        description_lower = (
            description or ""
        ).lower()

        exact_names = {
            str(value).lower()
            for value in condition.get(
                "name_exact",
                [],
            )
        }
        if (
            exact_names
            and name_lower not in exact_names
        ):
            return False

        name_regex = condition.get("name_regex")
        if (
            name_regex
            and not re.search(
                str(name_regex),
                name,
                re.IGNORECASE,
            )
        ):
            return False

        description_regex = condition.get(
            "description_regex"
        )
        if (
            description_regex
            and not re.search(
                str(description_regex),
                description or "",
                re.IGNORECASE,
            )
        ):
            return False

        allowed_types = {
            str(value).lower()
            for value in condition.get(
                "data_types",
                [],
            )
        }
        if (
            allowed_types
            and (data_type or "").lower()
            not in allowed_types
        ):
            return False

        contains_any = [
            str(value).lower()
            for value in condition.get(
                "contains_any",
                [],
            )
        ]
        if (
            contains_any
            and not any(
                token in name_lower
                or token in description_lower
                for token in contains_any
            )
        ):
            return False

        return bool(condition)
