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
