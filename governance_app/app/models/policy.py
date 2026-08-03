from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.job import JSON_TYPE, UUID_TYPE, utcnow


class GovernancePolicy(Base):
    __tablename__ = "governance_policies"
    __table_args__ = (
        UniqueConstraint("policy_key", name="uq_governance_policies_policy_key"),
        UniqueConstraint(
            "service",
            "name",
            name="uq_governance_policies_service_name",
        ),
        Index("ix_governance_policies_service_enabled", "service", "enabled"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        primary_key=True,
        default=uuid.uuid4,
    )
    policy_key: Mapped[str] = mapped_column(String(512), nullable=False)
    policy_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    service: Mapped[str] = mapped_column(String(255), nullable=False)
    service_type: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    document: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )
