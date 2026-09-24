from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ConfirmedTagEventRequest(BaseModel):
    """Normalized trigger emitted after OpenMetadata has confirmed tag state.

    Caller-provided tags are compatibility metadata only. The Backend worker
    always re-reads authoritative tag state from OpenMetadata before syncing
    Ranger.
    """

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
