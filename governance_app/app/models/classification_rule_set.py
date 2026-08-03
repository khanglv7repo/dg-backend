from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.job import JSON_TYPE, UUID_TYPE, utcnow


class ClassificationRuleSet(Base):
    __tablename__ = "classification_rule_sets"
    __table_args__ = (
        UniqueConstraint(
            "name",
            "document_sha256",
            name="uq_classification_rule_sets_name_sha256",
        ),
        Index(
            "ix_classification_rule_sets_name_status",
            "name",
            "status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default="default",
    )
    declared_version: Mapped[str | None] = mapped_column(String(255))
    document: Mapped[dict] = mapped_column(
        JSON_TYPE,
        nullable=False,
        default=dict,
    )
    document_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="INACTIVE",
    )
    created_by: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    created_by_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
    )
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
