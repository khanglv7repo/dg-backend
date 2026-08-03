from __future__ import annotations

from pydantic import BaseModel, Field


class ClassificationCommandRequest(BaseModel):
    entity_type: str = Field(default="table", min_length=1, max_length=64)
    entity_fqn: str = Field(min_length=1, max_length=1024)
    correlation_id: str | None = Field(default=None, max_length=128)
