from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.job import UUID_TYPE, utcnow


class IntegrationWatermark(Base):
    __tablename__ = "integration_watermarks"
    __table_args__ = (
        UniqueConstraint("system_name", "watermark_key", name="uq_integration_watermarks_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    system_name: Mapped[str] = mapped_column(String(64), nullable=False)
    watermark_key: Mapped[str] = mapped_column(String(128), nullable=False)
    watermark_value: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
