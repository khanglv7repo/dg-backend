from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class JobResponse(ORMModel):
    id: UUID
    job_type: str
    status: str
    priority: int
    idempotency_key: str
    correlation_id: str | None
    attempt_count: int
    max_attempts: int
    available_at: datetime
    locked_by: str | None
    last_error_code: str | None
    last_error_message: str | None
    created_at: datetime
    updated_at: datetime


class JobRetryRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class CeleryTaskStatusResponse(BaseModel):
    """Status response for Celery tasks dispatched via .delay() after the
    GovernanceJob→Celery migration (docs/13_IMPLEMENTATION_SPEC.md section 9).
    Returned by GET /api/v1/jobs/{task_id} when the ID is not a legacy DB row.
    """

    id: UUID
    source: str = "celery_task"
    status: str  # PENDING | STARTED | RETRY | SUCCESS | FAILURE
    info: str | None = None

