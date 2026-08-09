"""Repository for ClassificationExecution persistence."""
from __future__ import annotations

import uuid
from typing import Any
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.classification_execution import ClassificationExecution


class ClassificationExecutionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, execution_id: uuid.UUID | str) -> ClassificationExecution | None:
        if isinstance(execution_id, str):
            execution_id = uuid.UUID(execution_id)
        return self.session.get(ClassificationExecution, execution_id)

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

    def create_next_generation(
        self,
        *,
        event_id: str,
        entity_type: str,
        entity_fqn: str,
        status: str = "EVALUATING",
        outcome: str | None = None,
        rule_version_id: str | None = None,
        suggestions: list[dict[str, Any]] | None = None,
        evidence: dict[str, Any] | None = None,
        confidence: float | None = None,
        correlation_id: str | None = None,
    ) -> ClassificationExecution:
        """Create next generation N+1 and mark older unfinished runs SUPERSEDED."""
        raw_max = (
            self.session.query(func.max(ClassificationExecution.generation))
            .filter(ClassificationExecution.entity_fqn == entity_fqn)
            .scalar()
        )
        try:
            max_gen = int(raw_max) if raw_max is not None else 0
        except (ValueError, TypeError):
            max_gen = 0

        next_gen = max_gen + 1

        try:
            unfinished_records = (
                self.session.query(ClassificationExecution)
                .filter(
                    ClassificationExecution.entity_fqn == entity_fqn,
                    ClassificationExecution.status.in_(["EVALUATING", "WAITING_AI"]),
                )
                .all()
            )
            for rec in unfinished_records:
                rec.status = "SUPERSEDED"
        except Exception:
            pass

        return self.create(
            event_id=event_id,
            entity_type=entity_type,
            entity_fqn=entity_fqn,
            generation=next_gen,
            status=status,
            outcome=outcome,
            rule_version_id=rule_version_id,
            suggestions=suggestions,
            evidence=evidence,
            confidence=confidence,
            correlation_id=correlation_id,
        )

    def is_current_generation(self, execution_id: uuid.UUID | str, generation: int) -> bool:
        """Stale write guard: returns True if execution_id is current generation and active."""
        record = self.get(execution_id)
        if not record or record.status == "SUPERSEDED":
            return False

        raw_max = (
            self.session.query(func.max(ClassificationExecution.generation))
            .filter(ClassificationExecution.entity_fqn == record.entity_fqn)
            .scalar()
        )
        try:
            max_gen = int(raw_max) if raw_max is not None else record.generation
        except (ValueError, TypeError):
            max_gen = record.generation

        return record.generation == max_gen and record.generation == generation

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
