"""Repository for ClassificationExecution persistence."""
from __future__ import annotations

import uuid
from typing import Any
from sqlalchemy.orm import Session

from app.models.classification_execution import ClassificationExecution


class ClassificationExecutionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, execution_id: uuid.UUID | str) -> ClassificationExecution | None:
        if isinstance(execution_id, str):
            execution_id = uuid.UUID(execution_id)
        return self.session.query(ClassificationExecution).get(execution_id)

    def create(
        self,
        *,
        event_id: str,
        entity_type: str,
        entity_fqn: str,
        generation: int = 1,
        status: str = "EVALUATING",
        outcome: str | None = None,
        rule_version_id: str | None = None,
        suggestions: list[dict[str, Any]] | None = None,
        evidence: dict[str, Any] | None = None,
        confidence: float | None = None,
        correlation_id: str | None = None,
    ) -> ClassificationExecution:
        record = ClassificationExecution(
            event_id=event_id,
            entity_type=entity_type,
            entity_fqn=entity_fqn,
            generation=generation,
            status=status,
            outcome=outcome,
            rule_version_id=rule_version_id,
            suggestions=suggestions or [],
            evidence=evidence or {},
            confidence=confidence,
            correlation_id=correlation_id,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def update_status(
        self,
        execution_id: uuid.UUID | str,
        *,
        status: str,
        outcome: str | None = None,
        suggestions: list[dict[str, Any]] | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> ClassificationExecution:
        record = self.get(execution_id)
        if not record:
            raise ValueError(f"ClassificationExecution {execution_id} not found")
        record.status = status
        if outcome is not None:
            record.outcome = outcome
        if suggestions is not None:
            record.suggestions = suggestions
        if evidence is not None:
            record.evidence = evidence
        self.session.flush()
        return record
