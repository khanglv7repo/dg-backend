"""Repository for ClassificationExecution persistence."""
from __future__ import annotations

import uuid
from hashlib import sha256
from typing import Any
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.classification_execution import ClassificationExecution


class ClassificationExecutionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, execution_id: uuid.UUID | str) -> ClassificationExecution | None:
        if isinstance(execution_id, str):
            execution_id = uuid.UUID(execution_id)
        return self.session.get(ClassificationExecution, execution_id)

    def get_by_event_entity(
        self,
        *,
        event_id: str,
        entity_fqn: str,
    ) -> ClassificationExecution | None:
        idempotency_key = self._idempotency_key(event_id, entity_fqn)
        return (
            self.session.query(ClassificationExecution)
            .filter(
                (
                    ClassificationExecution.idempotency_key == idempotency_key
                )
                | (
                    (ClassificationExecution.idempotency_key.is_(None))
                    & (ClassificationExecution.event_id == event_id)
                    & (ClassificationExecution.entity_fqn == entity_fqn)
                ),
            )
            .first()
        )

    @staticmethod
    def _idempotency_key(event_id: str, entity_fqn: str) -> str:
        return sha256(f"{event_id}\x00{entity_fqn}".encode("utf-8")).hexdigest()

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
            idempotency_key=self._idempotency_key(event_id, entity_fqn),
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
        record, _created = self.get_or_create_next_generation(
            event_id=event_id,
            entity_type=entity_type,
            entity_fqn=entity_fqn,
            status=status,
            outcome=outcome,
            rule_version_id=rule_version_id,
            suggestions=suggestions,
            evidence=evidence,
            confidence=confidence,
            correlation_id=correlation_id,
        )
        return record

    def get_or_create_next_generation(
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
    ) -> tuple[ClassificationExecution, bool]:
        """Return one logical execution for event/entity or create next generation."""
        existing = self.get_by_event_entity(
            event_id=event_id,
            entity_fqn=entity_fqn,
        )
        if existing is not None:
            return existing, False

        for _attempt in range(3):
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

            try:
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
                ), True
            except IntegrityError:
                self.session.rollback()
                existing = self.get_by_event_entity(
                    event_id=event_id,
                    entity_fqn=entity_fqn,
                )
                if existing is not None:
                    return existing, False

        raise RuntimeError(
            f"could not create unique classification generation for {entity_fqn}"
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
