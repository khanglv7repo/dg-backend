from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.job import JSON_TYPE, UUID_TYPE, utcnow


class DataValueScanRun(Base):
    __tablename__ = "data_value_scan_runs"
    __table_args__ = (
        Index("ix_data_value_scan_runs_entity", "entity_fqn", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_fqn: Mapped[str] = mapped_column(String(1024), nullable=False)
    field_path: Mapped[str | None] = mapped_column(String(1024))
    scanner_version: Mapped[str] = mapped_column(String(255), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    total_samples: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    matched_samples: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confidence: Mapped[float | None] = mapped_column(Float)
    metrics: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    suggestions: Mapped[list] = mapped_column(JSON_TYPE, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="COMPLETED")
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
