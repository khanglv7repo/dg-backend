"""R6-B generation-fenced completion channel for AI classification executions."""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.repositories.audit import AuditRepository
from app.repositories.classification_execution import ClassificationExecutionRepository

CompletionStatus = Literal["COMPLETED", "NO_PROPOSAL"]

_MAX_RECOMMENDATIONS = 20
_MAX_MUTATIONS = 20
_ALLOWED_RESULT_KEYS = {"entity_type", "entity_fqn", "recommendations", "mutations"}
_ALLOWED_RECOMMENDATION_KEYS = {
    "tag",
    "confidence",
    "rationale",
    "field_path",
    "action_recommendation",
}
_ALLOWED_MUTATION_KEYS = {
    "status",
    "entity_fqn",
    "field_path",
    "tag_fqn",
    "mutation_count",
    "transport",
}
_TERMINAL_STATUSES = {"COMPLETED", "NO_PROPOSAL"}


def _bounded_text(
    value: Any,
    *,
    field: str,
    maximum: int,
    allow_none: bool = False,
) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    text = value.strip()
    if not text:
        raise ValidationError(f"{field} must not be empty")
    if len(text) > maximum:
        raise ValidationError(
            f"{field} exceeds maximum length",
            details={"field": field, "maximum": maximum},
        )
    return text


def _reject_unknown_keys(value: dict[str, Any], allowed: set[str], *, field: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValidationError(
            f"{field} contains unsupported keys",
            details={"field": field, "unsupported_keys": unknown},
        )


def _normalize_recommendation(value: Any, index: int) -> dict[str, Any]:
    field = f"result.recommendations[{index}]"
    if not isinstance(value, dict):
        raise ValidationError(f"{field} must be an object")
    _reject_unknown_keys(value, _ALLOWED_RECOMMENDATION_KEYS, field=field)

    tag = _bounded_text(value.get("tag"), field=f"{field}.tag", maximum=255)
    rationale = _bounded_text(
        value.get("rationale"),
        field=f"{field}.rationale",
        maximum=2000,
    )
    field_path = _bounded_text(
        value.get("field_path"),
        field=f"{field}.field_path",
        maximum=1024,
        allow_none=True,
    )

    confidence = value.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ValidationError(f"{field}.confidence must be a number")
    confidence = float(confidence)
    if not 0.0 <= confidence <= 1.0:
        raise ValidationError(f"{field}.confidence must be between 0 and 1")

    action = value.get("action_recommendation")
    if action != "APPLY":
        raise ValidationError(
            f"{field}.action_recommendation must be APPLY for completion"
        )

    return {
        "tag": tag,
        "confidence": confidence,
        "rationale": rationale,
        "field_path": field_path,
        "action_recommendation": "APPLY",
    }


def _normalize_mutation(value: Any, index: int) -> dict[str, Any]:
    field = f"result.mutations[{index}]"
    if not isinstance(value, dict):
        raise ValidationError(f"{field} must be an object")
    _reject_unknown_keys(value, _ALLOWED_MUTATION_KEYS, field=field)

    status = value.get("status")
    if status not in {"APPLIED", "NO_CHANGE"}:
        raise ValidationError(f"{field}.status must be APPLIED or NO_CHANGE")

    entity_fqn = _bounded_text(
        value.get("entity_fqn"),
        field=f"{field}.entity_fqn",
        maximum=1024,
    )
    field_path = _bounded_text(
        value.get("field_path"),
        field=f"{field}.field_path",
        maximum=1024,
        allow_none=True,
    )
    tag_fqn = _bounded_text(
        value.get("tag_fqn"),
        field=f"{field}.tag_fqn",
        maximum=255,
    )

    mutation_count = value.get("mutation_count")
    if isinstance(mutation_count, bool) or not isinstance(mutation_count, int):
        raise ValidationError(f"{field}.mutation_count must be an integer")
    if not 0 <= mutation_count <= 2:
        raise ValidationError(f"{field}.mutation_count must be between 0 and 2")
    if status == "NO_CHANGE" and mutation_count != 0:
        raise ValidationError(f"{field}.NO_CHANGE requires mutation_count=0")
    if status == "APPLIED" and mutation_count == 0:
        raise ValidationError(f"{field}.APPLIED requires mutation_count>0")

    transport_raw = value.get("transport")
    transport = None
    if transport_raw is not None:
        transport = _bounded_text(
            transport_raw,
            field=f"{field}.transport",
            maximum=64,
        )

    return {
        "status": status,
        "entity_fqn": entity_fqn,
        "field_path": field_path,
        "tag_fqn": tag_fqn,
        "mutation_count": mutation_count,
        **({"transport": transport} if transport is not None else {}),
    }


def _decision_fingerprint(
    *,
    status: CompletionStatus,
    entity_type: str,
    entity_fqn: str,
    recommendations: list[dict[str, Any]],
) -> str:
    identity = {
        "status": status,
        "entity_type": entity_type,
        "entity_fqn": entity_fqn,
        "targets": sorted(
            [
                {
                    "tag": item["tag"],
                    "field_path": item.get("field_path"),
                }
                for item in recommendations
            ],
            key=lambda item: (item["tag"], item.get("field_path") or ""),
        ),
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_result(
    *,
    status: CompletionStatus,
    result: Any,
) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise ValidationError("result must be an object")
    _reject_unknown_keys(result, _ALLOWED_RESULT_KEYS, field="result")

    entity_type = _bounded_text(
        result.get("entity_type"),
        field="result.entity_type",
        maximum=64,
    )
    entity_fqn = _bounded_text(
        result.get("entity_fqn"),
        field="result.entity_fqn",
        maximum=1024,
    )

    raw_recommendations = result.get("recommendations")
    if not isinstance(raw_recommendations, list):
        raise ValidationError("result.recommendations must be a list")
    if len(raw_recommendations) > _MAX_RECOMMENDATIONS:
        raise ValidationError(
            "result.recommendations exceeds server-side limit",
            details={"maximum": _MAX_RECOMMENDATIONS},
        )
    recommendations = [
        _normalize_recommendation(item, index)
        for index, item in enumerate(raw_recommendations)
    ]

    raw_mutations = result.get("mutations")
    if not isinstance(raw_mutations, list):
        raise ValidationError("result.mutations must be a list")
    if len(raw_mutations) > _MAX_MUTATIONS:
        raise ValidationError(
            "result.mutations exceeds server-side limit",
            details={"maximum": _MAX_MUTATIONS},
        )
    mutations = [
        _normalize_mutation(item, index)
        for index, item in enumerate(raw_mutations)
    ]

    if status == "NO_PROPOSAL":
        if recommendations or mutations:
            raise ValidationError(
                "NO_PROPOSAL completion requires empty recommendations and mutations"
            )
    else:
        if not recommendations:
            raise ValidationError("COMPLETED completion requires at least one recommendation")
        if len(mutations) != len(recommendations):
            raise ValidationError(
                "COMPLETED completion requires one verified mutation result per recommendation"
            )

        recommendation_targets = [
            (item["tag"], item.get("field_path")) for item in recommendations
        ]
        if len(set(recommendation_targets)) != len(recommendation_targets):
            raise ValidationError("duplicate recommendation target is not allowed")

        mutation_targets = [
            (item["tag_fqn"], item.get("field_path")) for item in mutations
        ]
        if len(set(mutation_targets)) != len(mutation_targets):
            raise ValidationError("duplicate mutation target is not allowed")

        if sorted(recommendation_targets, key=lambda item: (item[0], item[1] or "")) != sorted(
            mutation_targets,
            key=lambda item: (item[0], item[1] or ""),
        ):
            raise ValidationError(
                "mutation targets must exactly match APPLY recommendation targets"
            )

        if any(item["entity_fqn"] != entity_fqn for item in mutations):
            raise ValidationError(
                "every mutation entity_fqn must match result.entity_fqn"
            )

    return {
        "version": 1,
        "status": status,
        "entity_type": entity_type,
        "entity_fqn": entity_fqn,
        "recommendations": recommendations,
        "mutations": mutations,
        "decision_fingerprint": _decision_fingerprint(
            status=status,
            entity_type=entity_type,
            entity_fqn=entity_fqn,
            recommendations=recommendations,
        ),
    }


class ClassificationCompletionService:
    """Complete one already-dispatched WAITING_AI generation exactly once.

    This is continuation/recovery of an existing generation, not creation of new
    policy intent, so there is deliberately no human confirmation flag here.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.executions = ClassificationExecutionRepository(session)
        self.audit = AuditRepository(session)

    @staticmethod
    def _stale_response(
        *,
        execution_id: str,
        requested_generation: int,
        record_generation: int,
        current_generation: int | None,
        current_status: str,
    ) -> dict[str, Any]:
        return {
            "status": "SUPERSEDED",
            "execution_id": execution_id,
            "generation": requested_generation,
            "record_generation": record_generation,
            "current_generation": current_generation,
            "current_status": current_status,
            "authority_changed": False,
            "duplicate": False,
            "stale": True,
        }

    def complete(
        self,
        *,
        execution_id: str,
        generation: int,
        status: CompletionStatus,
        result: dict[str, Any],
        actor_id: str,
        actor_name: str,
    ) -> dict[str, Any]:
        if status not in _TERMINAL_STATUSES:
            raise ValidationError("status must be COMPLETED or NO_PROPOSAL")
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
            raise ValidationError("generation must be a positive integer")

        try:
            identifier = uuid.UUID(str(execution_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValidationError("execution_id must be a UUID") from exc

        normalized = _normalize_result(status=status, result=result)

        record = self.executions.get_for_update(identifier)
        if record is None:
            raise NotFoundError(f"classification execution {identifier} was not found")

        current_generation = self.executions.current_generation(record.entity_fqn)
        if (
            record.status == "SUPERSEDED"
            or record.generation != generation
            or current_generation != generation
        ):
            return self._stale_response(
                execution_id=str(record.id),
                requested_generation=generation,
                record_generation=record.generation,
                current_generation=current_generation,
                current_status=record.status,
            )

        if normalized["entity_type"] != record.entity_type:
            raise ValidationError(
                "result.entity_type does not match the execution",
                details={
                    "expected": record.entity_type,
                    "observed": normalized["entity_type"],
                },
            )
        if normalized["entity_fqn"] != record.entity_fqn:
            raise ValidationError(
                "result.entity_fqn does not match the execution",
                details={
                    "expected": record.entity_fqn,
                    "observed": normalized["entity_fqn"],
                },
            )

        existing_completion = (
            (record.evidence or {}).get("ai_completion")
            if isinstance(record.evidence, dict)
            else None
        )
        if record.status in _TERMINAL_STATUSES:
            existing_fingerprint = (
                existing_completion.get("decision_fingerprint")
                if isinstance(existing_completion, dict)
                else None
            )
            if (
                record.status == status
                and existing_fingerprint == normalized["decision_fingerprint"]
            ):
                return {
                    "status": record.status,
                    "execution_id": str(record.id),
                    "generation": record.generation,
                    "authority_changed": False,
                    "duplicate": True,
                    "stale": False,
                    "decision_fingerprint": existing_fingerprint,
                    "recommendation_count": len(record.suggestions or []),
                    "om_mutation_count": sum(
                        int(item.get("mutation_count", 0))
                        for item in (existing_completion or {}).get("mutations", [])
                        if isinstance(item, dict)
                    ),
                }
            raise ConflictError(
                "classification execution already has a conflicting terminal completion",
                details={
                    "execution_id": str(record.id),
                    "current_status": record.status,
                    "requested_status": status,
                },
            )

        if record.status != "WAITING_AI":
            raise ConflictError(
                "classification execution is not WAITING_AI",
                details={
                    "execution_id": str(record.id),
                    "current_status": record.status,
                },
            )

        evidence = dict(record.evidence or {})
        evidence["ai_completion"] = {
            **normalized,
            "generation": generation,
        }
        recommendations = normalized["recommendations"]
        confidence = (
            max(item["confidence"] for item in recommendations)
            if recommendations
            else None
        )

        self.executions.update_status(
            record.id,
            status=status,
            suggestions=recommendations,
            evidence=evidence,
            confidence=confidence,
        )

        om_mutation_count = sum(
            int(item.get("mutation_count", 0)) for item in normalized["mutations"]
        )
        self.audit.record(
            actor_id=actor_id,
            actor_name=actor_name,
            action=(
                "AI_CLASSIFICATION_COMPLETED"
                if status == "COMPLETED"
                else "AI_CLASSIFICATION_NO_PROPOSAL"
            ),
            object_type=record.entity_type,
            object_id=record.entity_fqn,
            correlation_id=record.correlation_id,
            details={
                "execution_id": str(record.id),
                "generation": generation,
                "status": status,
                "decision_fingerprint": normalized["decision_fingerprint"],
                "recommendation_count": len(recommendations),
                "om_mutation_count": om_mutation_count,
                "mutation_statuses": [item["status"] for item in normalized["mutations"]],
            },
        )
        self.session.flush()

        return {
            "status": status,
            "execution_id": str(record.id),
            "generation": generation,
            "authority_changed": True,
            "duplicate": False,
            "stale": False,
            "decision_fingerprint": normalized["decision_fingerprint"],
            "recommendation_count": len(recommendations),
            "om_mutation_count": om_mutation_count,
        }
