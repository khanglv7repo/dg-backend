
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.job import JSON_TYPE, UUID_TYPE, utcnow


class ClassificationRun(Base):
    __tablename__ = "classification_runs"
    __table_args__ = (
        UniqueConstraint(
            "event_id", "source_kind", "source_version", name="uq_classification_run_source"
        ),
        Index("ix_classification_runs_entity_created", "entity_fqn", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_fqn: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_version: Mapped[str] = mapped_column(String(255), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    suggestions: Mapped[list] = mapped_column(JSON_TYPE, nullable=False, default=list)
    evidence: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    openmetadata_suggestion_ids: Mapped[list] = mapped_column(
        JSON_TYPE, nullable=False, default=list
    )
    confidence: Mapped[float | None] = mapped_column(Float)
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
