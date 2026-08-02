from __future__ import annotations

import hashlib

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ConfigurationError
from app.models.enums import ClassificationAction, ClassificationSource, JobType
from app.repositories.audit import AuditRepository
from app.repositories.classification import ClassificationRunRepository
from app.repositories.jobs import JobRepository
from app.rules.classification import ClassificationRuleEngine
from app.schemas.classification import MatchOutcome, TagSuggestion
from app.schemas.events import AgentClassificationEventRequest, MetadataEventRequest


def change_from_suggestions(suggestions: list[TagSuggestion]) -> dict:
    entity_tags: list[str] = []
    field_tags: dict[str, list[str]] = {}
    for item in suggestions:
        if item.field_path:
            field_tags.setdefault(item.field_path, []).append(item.tag)
        else:
            entity_tags.append(item.tag)
    return {
        "entity_tags": sorted(set(entity_tags)),
        "field_tags": {key: sorted(set(values)) for key, values in sorted(field_tags.items())},
    }


def _minimum_confidence(suggestions: list[TagSuggestion]) -> float | None:
    return min((item.confidence for item in suggestions), default=None)


class ClassificationService:
    """Deterministic classification and optional Phase 2 Agent fallback."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.audit = AuditRepository(session)
        self.runs = ClassificationRunRepository(session)

    def classify(self, event: MetadataEventRequest) -> dict:
        engine = ClassificationRuleEngine.from_path(
            self.settings.resolve_path(self.settings.classification_rules_path)
        )
        result = engine.evaluate(event)
        action = self._select_action(result)
        run = self.runs.create(
            event_id=event.event_id,
            entity_type=event.entity_type,
            entity_fqn=event.entity_fqn,
            source_kind=ClassificationSource.DETERMINISTIC.value,
            source_version=result.rule_version,
            outcome=result.outcome.value,
            action=action.value,
            suggestions=[item.model_dump(mode="json") for item in result.suggestions],
            evidence=result.evidence,
            confidence=_minimum_confidence(result.suggestions),
            correlation_id=event.correlation_id,
        )
        self.audit.record(
            actor_id="system:deterministic-rules",
            actor_name="Deterministic Rule Engine",
            action="CLASSIFICATION_EVALUATED",
            object_type=event.entity_type,
            object_id=event.entity_fqn,
            correlation_id=event.correlation_id,
            details={
                "classification_run_id": str(run.id),
                **result.model_dump(mode="json"),
                "selected_action": action.value,
            },
        )

        if action == ClassificationAction.NONE:
            return {"outcome": result.outcome.value, "action": action.value, "run_id": str(run.id)}

        if action == ClassificationAction.AGENT_FALLBACK:
            logical = hashlib.sha256(
                f"{event.event_id}|{event.entity_fqn}|{self.settings.agent_graph_version}".encode()
            ).hexdigest()
            job = JobRepository(self.session).enqueue(
                job_type=JobType.AGENT_CLASSIFY,
                idempotency_key=f"agent-classify:{logical}",
                payload=event.model_dump(mode="json"),
                correlation_id=event.correlation_id,
                max_attempts=3,
            )
            return {
                "outcome": result.outcome.value,
                "action": action.value,
                "run_id": str(run.id),
                "job_id": str(job.id),
            }

        if action == ClassificationAction.SAMPLE_VALUE_FALLBACK:
            logical = hashlib.sha256(
                f"{event.event_id}|{event.entity_fqn}|sample-scan".encode()
            ).hexdigest()
            job = JobRepository(self.session).enqueue(
                job_type=JobType.SAMPLE_COLUMN_VALUES,
                idempotency_key=f"sample-scan:{logical}",
                payload=event.model_dump(mode="json"),
                correlation_id=event.correlation_id,
                max_attempts=3,
            )
            return {
                "outcome": result.outcome.value,
                "action": action.value,
                "run_id": str(run.id),
                "job_id": str(job.id),
            }

        change = change_from_suggestions(result.suggestions)
        job_type = (
            JobType.APPLY_CONFIRMED_TAGS
            if action == ClassificationAction.AUTO_APPLY
            else JobType.CREATE_OM_SUGGESTIONS
        )
        logical = hashlib.sha256(f"{run.id}|{action.value}|{change}".encode()).hexdigest()
        job = JobRepository(self.session).enqueue(
            job_type=job_type,
            idempotency_key=f"classification-action:{logical}",
            payload={
                **change,
                "classification_run_id": str(run.id),
                "entity_type": event.entity_type,
                "entity_fqn": event.entity_fqn,
                "source_kind": ClassificationSource.DETERMINISTIC.value,
                "source_version": result.rule_version,
                "suggestions": [item.model_dump(mode="json") for item in result.suggestions],
                "correlation_id": event.correlation_id,
            },
            correlation_id=event.correlation_id,
        )
        return {
            "outcome": result.outcome.value,
            "action": action.value,
            "run_id": str(run.id),
            "job_id": str(job.id),
        }

    def _select_action(self, result) -> ClassificationAction:
        if self.settings.agent_enabled and result.outcome in {
            MatchOutcome.NO_MATCH,
            MatchOutcome.AMBIGUOUS,
        }:
            return ClassificationAction.AGENT_FALLBACK
        if self.settings.sample_scan_enabled and result.outcome in {
            MatchOutcome.NO_MATCH,
            MatchOutcome.AMBIGUOUS,
        }:
            return ClassificationAction.SAMPLE_VALUE_FALLBACK
        if result.outcome == MatchOutcome.NO_MATCH:
            return ClassificationAction.NONE
        if (
            result.outcome == MatchOutcome.EXACT
            and result.trusted_auto_apply
            and self.settings.trusted_auto_apply_enabled
        ):
            return ClassificationAction.AUTO_APPLY
        return ClassificationAction.OPENMETADATA_SUGGESTION


class AgentClassificationResultService:
    """Validate an Agent Worker result and enqueue native OM Suggestions."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.audit = AuditRepository(session)
        self.runs = ClassificationRunRepository(session)

    def accept(self, request: AgentClassificationEventRequest) -> dict:
        if not self.settings.agent_enabled:
            raise ConfigurationError("Agent Worker is disabled")

        engine = ClassificationRuleEngine.from_path(
            self.settings.resolve_path(self.settings.classification_rules_path)
        )
        allowed_tags = {str(rule["tag"]) for rule in engine.rules if rule.get("tag")}
        proposed_tags = {item.tag for item in request.suggestions}
        disallowed = sorted(proposed_tags - allowed_tags)
        if disallowed:
            raise ConfigurationError(
                "Agent proposed tags outside the governed allow-list: " + ", ".join(disallowed)
            )

        action = (
            ClassificationAction.OPENMETADATA_SUGGESTION
            if request.suggestions
            else ClassificationAction.NONE
        )
        source_version = (
            f"{request.agent_name}:{request.graph_version}:"
            f"{request.model}:{request.prompt_version}"
        )
        run = self.runs.create(
            event_id=request.event_id,
            entity_type=request.entity_type,
            entity_fqn=request.entity_fqn,
            source_kind=ClassificationSource.AGENT.value,
            source_version=source_version,
            outcome=(MatchOutcome.EXACT.value if request.suggestions else MatchOutcome.NO_MATCH.value),
            action=action.value,
            suggestions=[item.model_dump(mode="json") for item in request.suggestions],
            evidence={
                "input_fingerprint": request.input_fingerprint,
                "graph_version": request.graph_version,
                "prompt_version": request.prompt_version,
                **request.evidence,
            },
            confidence=_minimum_confidence(request.suggestions),
            correlation_id=request.correlation_id,
        )
        self.audit.record(
            actor_id=f"bot:{self.settings.openmetadata_agent_bot_name}",
            actor_name=self.settings.openmetadata_agent_bot_name,
            action="AGENT_CLASSIFICATION_COMPLETED",
            object_type=request.entity_type,
            object_id=request.entity_fqn,
            correlation_id=request.correlation_id,
            details={
                "classification_run_id": str(run.id),
                "model": request.model,
                "graph_version": request.graph_version,
                "suggestion_count": len(request.suggestions),
            },
        )
        if not request.suggestions:
            return {"action": action.value, "run_id": str(run.id), "job_id": None}

        change = change_from_suggestions(request.suggestions)
        key = hashlib.sha256(f"{run.id}|om-suggestions|{change}".encode()).hexdigest()
        job = JobRepository(self.session).enqueue(
            job_type=JobType.CREATE_OM_SUGGESTIONS,
            idempotency_key=f"agent-suggestions:{key}",
            payload={
                **change,
                "classification_run_id": str(run.id),
                "entity_type": request.entity_type,
                "entity_fqn": request.entity_fqn,
                "source_kind": ClassificationSource.AGENT.value,
                "source_version": source_version,
                "suggestions": [item.model_dump(mode="json") for item in request.suggestions],
                "correlation_id": request.correlation_id,
            },
            correlation_id=request.correlation_id,
        )
        return {"action": action.value, "run_id": str(run.id), "job_id": str(job.id)}


# Compatibility alias for code written against v0.3.
AgentClassificationIntakeService = AgentClassificationResultService
