
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ClassificationRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_id: str
    entity_type: str
    entity_fqn: str
    source_kind: str
    source_version: str
    outcome: str
    action: str
    suggestions: list
    evidence: dict
    openmetadata_suggestion_ids: list
    confidence: float | None
    correlation_id: str | None
    created_at: datetime
    updated_at: datetime
