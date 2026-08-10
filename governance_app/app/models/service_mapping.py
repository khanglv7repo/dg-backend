from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.job import UUID_TYPE, utcnow


class ServiceMapping(Base):
    """Backend-owned exact service mapping defined by the accepted context."""

    __tablename__ = "service_mapping"
    __table_args__ = (
        UniqueConstraint(
            "om_service_name",
            "environment",
            name="uq_service_mapping_om_service_environment",
        ),
        Index("ix_service_mapping_trino_catalog", "trino_catalog"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        primary_key=True,
        default=uuid.uuid4,
    )
    om_service_name: Mapped[str] = mapped_column(String(255), nullable=False)
    trino_catalog: Mapped[str] = mapped_column(String(255), nullable=False)
    ranger_service_name: Mapped[str] = mapped_column(String(255), nullable=False)
    ranger_tag_service_name: Mapped[str | None] = mapped_column(String(255))
    environment: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)
