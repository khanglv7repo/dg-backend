from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.job import JSON_TYPE, UUID_TYPE, utcnow


class EventInbox(Base):
    """Durable event intake store for OpenMetadata webhooks."""

    __tablename__ = "event_inbox"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_event_inbox_event_id"),
        Index("ix_event_inbox_entity", "entity_type", "entity_fqn", "created_at"),
        Index("ix_event_inbox_status", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_fqn: Mapped[str] = mapped_column(String(1024), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    purposes: Mapped[list] = mapped_column(JSON_TYPE, nullable=False, default=list)
    dispatched_purposes: Mapped[list] = mapped_column(JSON_TYPE, nullable=False, default=list)
    dispatched_tasks: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="RECEIVED")
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
