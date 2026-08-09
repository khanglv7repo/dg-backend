from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.job import JSON_TYPE, UUID_TYPE, utcnow


class ClassificationExecution(Base):
    """Classification execution persistence tracking deterministic & AI fallback decisions."""

    __tablename__ = "classification_executions"
    __table_args__ = (
        UniqueConstraint(
            "entity_fqn",
            "generation",
            name="uq_classification_execution_entity_generation",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_classification_execution_idempotency_key",
        ),
        Index("ix_classification_executions_entity", "entity_fqn", "created_at"),
        Index("ix_classification_executions_status", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_fqn: Mapped[str] = mapped_column(String(1024), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="EVALUATING")
    outcome: Mapped[str | None] = mapped_column(String(32))
    rule_version_id: Mapped[str | None] = mapped_column(String(255))
    suggestions: Mapped[list] = mapped_column(JSON_TYPE, nullable=False, default=list)
    evidence: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    confidence: Mapped[float | None] = mapped_column(Float)
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
