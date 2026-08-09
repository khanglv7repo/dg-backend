from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.job import JSON_TYPE, UUID_TYPE, utcnow


class TagSyncState(Base):
    """Tracks current Ranger tag synchronization state per entity."""

    __tablename__ = "tag_sync_states"
    __table_args__ = (
        UniqueConstraint("entity_type", "entity_fqn", name="uq_tag_sync_states_entity"),
        Index("ix_tag_sync_states_status", "status", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_fqn: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    checksum: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
