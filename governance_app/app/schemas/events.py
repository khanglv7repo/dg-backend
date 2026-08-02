from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.classification import TagSuggestion


class MetadataField(BaseModel):
    name: str
    data_type: str | None = None
    description: str | None = None
    sample_values: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("sample_values")
    @classmethod
    def bound_samples(cls, values: list[str]) -> list[str]:
        return [str(value)[:128] for value in values]


class MetadataEventRequest(BaseModel):
    """Normalized asset event used by the deterministic classifier."""

    event_id: str = Field(min_length=1, max_length=255)
    event_type: Literal["ENTITY_CREATED", "ENTITY_UPDATED", "MANUAL_CLASSIFICATION"]
    entity_type: str = Field(default="table", min_length=1, max_length=64)
    entity_fqn: str = Field(min_length=1, max_length=1024)
    entity_name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    fields: list[MetadataField] = Field(default_factory=list, max_length=1000)
    existing_tags: list[str] = Field(default_factory=list, max_length=200)
    raw_event: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = Field(default=None, max_length=128)


class AgentClassificationEventRequest(BaseModel):
    """Internal structured result produced by the Agent Worker.

    It is persisted through the same PostgreSQL transaction boundary; there is
    no second FastAPI application and no HTTP hand-off between workers.
    """

    event_id: str = Field(min_length=1, max_length=255)
    entity_type: str = Field(default="table", min_length=1, max_length=64)
    entity_fqn: str = Field(min_length=1, max_length=1024)
    agent_name: str = Field(min_length=1, max_length=255)
    graph_version: str = Field(min_length=1, max_length=255)
    model: str = Field(min_length=1, max_length=255)
    prompt_version: str = Field(min_length=1, max_length=255)
    input_fingerprint: str = Field(min_length=8, max_length=128)
    suggestions: list[TagSuggestion] = Field(default_factory=list, max_length=200)
    evidence: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = Field(default=None, max_length=128)


class ConfirmedTagEventRequest(BaseModel):
    """Normalized event emitted after OpenMetadata has confirmed tag state."""

    event_id: str = Field(min_length=1, max_length=255)
    source: Literal[
        "SUGGESTION_ACCEPTED",
        "AUTOMATED_TAG_CONFIRMED",
        "MANUAL_TAG_CONFIRMED",
        "RECONCILIATION",
    ]
    entity_type: str = Field(default="table", min_length=1, max_length=64)
    entity_fqn: str = Field(min_length=1, max_length=1024)
    tags: list[str] = Field(default_factory=list, max_length=200)
    field_paths: dict[str, list[str]] = Field(default_factory=dict)
    raw_event: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = Field(default=None, max_length=128)
