from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, ValidationError
from app.repositories.classification_execution import ClassificationExecutionRepository
from app.repositories.jobs import JobRepository


class WorkflowQueryService:
    """Bounded read facade over existing durable execution/job state."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.classification = ClassificationExecutionRepository(session)
        self.jobs = JobRepository(session)

    def get(self, execution_id: str) -> dict:
        try:
            identifier = uuid.UUID(str(execution_id))
        except ValueError as exc:
            raise ValidationError("execution_id must be a UUID") from exc

        classification = self.classification.get(identifier)
        if classification is not None:
            return {
                "source": "classification_execution",
                "id": str(classification.id),
                "event_id": classification.event_id,
                "entity_type": classification.entity_type,
                "entity_fqn": classification.entity_fqn,
                "generation": classification.generation,
                "status": classification.status,
                "outcome": classification.outcome,
                "confidence": classification.confidence,
                "correlation_id": classification.correlation_id,
                "created_at": classification.created_at,
                "updated_at": classification.updated_at,
            }

        try:
            job = self.jobs.get(identifier)
        except NotFoundError as exc:
            raise NotFoundError(f"workflow/execution {identifier} was not found") from exc

        # Do not expose arbitrary job payloads: they may contain operational data
        # not needed for status polling.
        return {
            "source": "governance_job",
            "id": str(job.id),
            "job_type": job.job_type,
            "status": job.status,
            "attempt_count": job.attempt_count,
            "max_attempts": job.max_attempts,
            "last_error_code": job.last_error_code,
            "correlation_id": job.correlation_id,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "available_at": job.available_at,
        }
