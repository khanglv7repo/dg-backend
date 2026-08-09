from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class OpenMetadataChangeEventRequest(BaseModel):
    """Official OpenMetadata ChangeEvent payload per OpenMetadata OpenAPI spec."""

    id: str | None = Field(default=None)
    eventId: str | None = Field(default=None)
    eventType: str = Field(min_length=1, max_length=128)
    entityType: str = Field(default="table", min_length=1, max_length=64)
    entityId: str | None = Field(default=None)
    domains: list[dict[str, Any]] | None = Field(default=None)
    entityFullyQualifiedName: str | None = Field(default=None)
    entityFQN: str | None = Field(default=None)
    userName: str | None = Field(default=None)
    timestamp: int | float | None = Field(default=None)
    changeDescription: dict[str, Any] = Field(default_factory=dict)
    incrementalChangeDescription: dict[str, Any] = Field(default_factory=dict)
    entity: dict[str, Any] = Field(default_factory=dict)
    previousVersion: float | int | str | None = Field(default=None)
    currentVersion: float | int | str | None = Field(default=None)


class OpenMetadataWebhookResponse(BaseModel):
    status: str = "accepted"
    event_id: str | None = None
    purposes: list[str] = Field(default_factory=list)
    dispatched_tasks: list[str] = Field(default_factory=list)
