"""Celery tasks for entity classification (R3 TAG Vertical Slice).

Handles deterministic rule evaluation, direct OpenMetadata Confirmed tag mutations on MATCH,
read-back verification, and handoff to ai.classification queue on NO_MATCH/AMBIGUOUS/CONFLICT.
"""
from __future__ import annotations

import logging
from typing import Any

from app.celery_app import app
from app.clients.openmetadata import OpenMetadataClient
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.schemas.classification import MatchOutcome
from app.repositories.audit import AuditRepository
from app.repositories.classification_execution import ClassificationExecutionRepository
from app.rules.classification import ClassificationRuleEngine
from app.schemas.events import MetadataEventRequest, MetadataField
from app.services.classification_rule_catalog import ClassificationRuleCatalogService

logger = logging.getLogger(__name__)


def _extract_fields_from_entity(entity: dict[str, Any]) -> list[MetadataField]:
    fields: list[MetadataField] = []
    for column in entity.get("columns", []) or []:
        if isinstance(column, dict) and column.get("name"):
            fields.append(
                MetadataField(
                    name=column["name"],
                    data_type=column.get("dataType"),
                    description=column.get("description"),
                    sample_values=[],
                )
            )
    return fields


@app.task(name="app.tasks.classification.classify_entity", bind=True, max_retries=3)
def classify_entity(
    self,
    *,
    event_id: str,
    entity_type: str,
    entity_fqn: str,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Run deterministic classification on an entity using latest OpenMetadata state."""
    settings = get_settings()
    om_client = OpenMetadataClient(
        base_url=settings.openmetadata_base_url,
        token=(
            settings.openmetadata_auto_tag_bot_token.get_secret_value()
            if settings.openmetadata_auto_tag_bot_token
            else settings.openmetadata_execution_bot_token.get_secret_value()
            if settings.openmetadata_execution_bot_token
            else None
        ),
        timeout=settings.openmetadata_timeout_seconds,
    )

    with SessionLocal() as session:
        exec_repo = ClassificationExecutionRepository(session)
        audit_repo = AuditRepository(session)

        try:
            catalog_service = ClassificationRuleCatalogService(session)
            active_rule_version = catalog_service.version_repo.get_active()
            if active_rule_version is None:
                active_rule_version = catalog_service.get_active()

            rule_version_id = str(active_rule_version.id)
            rule_document = getattr(active_rule_version, "payload", None) or getattr(
                active_rule_version,
                "document",
                None,
            )
            captured_authoritative_rule_version = (
                getattr(active_rule_version, "payload", None) is not None
            )
            engine = ClassificationRuleEngine(rule_document)

            # Create or reuse one logical execution per OM event/entity.
            execution, created = exec_repo.get_or_create_next_generation(
                event_id=event_id,
                entity_type=entity_type,
                entity_fqn=entity_fqn,
                status="EVALUATING",
                rule_version_id=rule_version_id,
                correlation_id=correlation_id,
            )
            if not created:
                ai_handoff_republished = False
                if execution.status == "WAITING_AI":
                    ai_classify_entity.delay(
                        execution_id=str(execution.id),
                        generation=execution.generation,
                    )
                    ai_handoff_republished = True
                return {
                    "status": execution.status,
                    "outcome": execution.outcome,
                    "execution_id": str(execution.id),
                    "generation": execution.generation,
                    "duplicate": True,
                    "ai_handoff_republished": ai_handoff_republished,
                }

            # 1. Re-read latest OpenMetadata entity state (Latest-state rule)
            latest_entity = om_client.get_entity(
                entity_type=entity_type,
                fqn=entity_fqn,
                fields="tags,columns",
            )
            entity_name = str(latest_entity.get("name") or entity_fqn.split(".")[-1])
            description = latest_entity.get("description")
            fields = _extract_fields_from_entity(latest_entity)

            normalized_event = MetadataEventRequest(
                event_id=event_id,
                event_type="ENTITY_UPDATED",
                entity_type=entity_type,
                entity_fqn=entity_fqn,
                entity_name=entity_name,
                description=description,
                fields=fields,
                existing_tags=[],
                correlation_id=correlation_id,
            )

            # 2. Evaluate the captured immutable active rule version.
            eval_result = engine.evaluate(normalized_event)

            outcome_str = eval_result.outcome.value

            if eval_result.outcome == MatchOutcome.EXACT:
                # Deterministic MATCH -> Write Confirmed tag directly to OpenMetadata
                entity_tags: list[str] = []
                field_tags: dict[str, list[str]] = {}

                for sugg in eval_result.suggestions:
                    if sugg.field_path:
                        field_tags.setdefault(sugg.field_path, []).append(sugg.tag)
                    else:
                        entity_tags.append(sugg.tag)

                all_tags = set(entity_tags)
                for tags in field_tags.values():
                    all_tags.update(tags)

                # Validate tags exist in OpenMetadata taxonomy
                om_client.validate_tag_fqns(list(all_tags))

                if not exec_repo.is_current_generation(execution.id, execution.generation):
                    exec_repo.update_status(
                        execution.id,
                        status="SUPERSEDED",
                        outcome="MATCH",
                        suggestions=[s.model_dump(mode="json") for s in eval_result.suggestions],
                        evidence={
                            **eval_result.evidence,
                            "stale_reason": "generation_not_current",
                        },
                    )
                    session.commit()
                    return {
                        "status": "SUPERSEDED",
                        "outcome": "MATCH",
                        "execution_id": str(execution.id),
                        "generation": execution.generation,
                        "reason": "generation_not_current",
                    }

                if (
                    captured_authoritative_rule_version
                    and not catalog_service.version_repo.is_active(rule_version_id)
                ):
                    exec_repo.update_status(
                        execution.id,
                        status="SUPERSEDED",
                        outcome="MATCH",
                        suggestions=[s.model_dump(mode="json") for s in eval_result.suggestions],
                        evidence={
                            **eval_result.evidence,
                            "stale_reason": "rule_version_not_active",
                            "captured_rule_version_id": rule_version_id,
                        },
                    )
                    session.commit()
                    return {
                        "status": "SUPERSEDED",
                        "outcome": "MATCH",
                        "execution_id": str(execution.id),
                        "generation": execution.generation,
                        "rule_version_id": rule_version_id,
                        "reason": "rule_version_not_active",
                    }

                # Apply Confirmed tags directly
                observed = om_client.apply_confirmed_tags(
                    entity_type=entity_type,
                    entity_fqn=entity_fqn,
                    entity_tags=entity_tags,
                    field_tags=field_tags,
                    label_type="Automated",
                )

                # Read-back assertion
                om_client.assert_confirmed_tags(
                    observed,
                    entity_tags=entity_tags,
                    field_tags=field_tags,
                )

                # Mark execution as COMPLETED
                exec_repo.update_status(
                    execution.id,
                    status="COMPLETED",
                    outcome="MATCH",
                    suggestions=[s.model_dump(mode="json") for s in eval_result.suggestions],
                    evidence=eval_result.evidence,
                )

                audit_repo.record(
                    actor_id="bot:governance-execution-bot",
                    actor_name="Governance Execution Bot",
                    action="DETERMINISTIC_MATCH_MUTATED_OM",
                    object_type=entity_type,
                    object_id=entity_fqn,
                    correlation_id=correlation_id,
                    details={
                        "execution_id": str(execution.id),
                        "outcome": "MATCH",
                        "applied_entity_tags": entity_tags,
                        "applied_field_tags": field_tags,
                    },
                )
                session.commit()

                return {
                    "status": "COMPLETED",
                    "outcome": "MATCH",
                    "execution_id": str(execution.id),
                    "generation": execution.generation,
                    "rule_version_id": rule_version_id,
                }

            else:
                # NO_MATCH, AMBIGUOUS, or CONFLICT -> Transition to WAITING_AI & enqueue on ai.classification queue
                exec_repo.update_status(
                    execution.id,
                    status="WAITING_AI",
                    outcome=outcome_str,
                    suggestions=[s.model_dump(mode="json") for s in eval_result.suggestions],
                    evidence=eval_result.evidence,
                )

                audit_repo.record(
                    actor_id="system:classification-engine",
                    actor_name="Classification Engine",
                    action="AI_FALLBACK_ENQUEUED",
                    object_type=entity_type,
                    object_id=entity_fqn,
                    correlation_id=correlation_id,
                    details={
                        "execution_id": str(execution.id),
                        "generation": execution.generation,
                        "outcome": outcome_str,
                    },
                )
                session.commit()

                # Enqueue on ai.classification queue
                ai_classify_entity.delay(
                    execution_id=str(execution.id),
                    generation=execution.generation,
                )

                return {
                    "status": "WAITING_AI",
                    "outcome": outcome_str,
                    "execution_id": str(execution.id),
                    "generation": execution.generation,
                    "rule_version_id": rule_version_id,
                }

        except Exception as exc:
            session.rollback()
            logger.exception("classify_entity failed for %s: %s", entity_fqn, exc)
            raise self.retry(exc=exc, countdown=10)
        finally:
            om_client.close()


@app.task(
    name="app.tasks.classification.ai_classify_entity",
    queue="ai.classification",
    bind=True,
    max_retries=2,
)
def ai_classify_entity(
    self,
    *,
    execution_id: str,
    generation: int,
) -> dict[str, Any]:
    """AI fallback handoff task.

    Receives minimal durable identity: execution_id + generation.
    Remains WAITING_AI during R3 (Agent consumer implemented in R6).
    """
    logger.info(
        "ai_classify_entity handoff received for execution_id=%s generation=%d",
        execution_id,
        generation,
    )
    return {
        "status": "WAITING_AI",
        "execution_id": execution_id,
        "generation": generation,
    }
