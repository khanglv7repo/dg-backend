from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AcceptedResponse(BaseModel):
    job_id: UUID
    status: str = "QUEUED"


class HealthResponse(BaseModel):
    status: str
    timestamp: datetime
