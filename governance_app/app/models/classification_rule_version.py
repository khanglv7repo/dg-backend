from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.job import JSON_TYPE, UUID_TYPE, utcnow


class ClassificationRuleVersion(Base):
    """Authoritative classification_rule_version table per architecture spec."""

    __tablename__ = "classification_rule_versions"
    __table_args__ = (
        UniqueConstraint("rule_key", "version", name="uq_classification_rule_version_key_ver"),
        Index("ix_classification_rule_versions_key_status", "rule_key", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    rule_key: Mapped[str] = mapped_column(String(255), nullable=False, default="default")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="INACTIVE")
    payload: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    declared_version: Mapped[str | None] = mapped_column(String(255))
    created_by: Mapped[str] = mapped_column(String(255), nullable=False, default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def name(self) -> str:
        return self.rule_key

    @property
    def document(self) -> dict:
        return self.payload

    @property
    def document_sha256(self) -> str:
        return self.checksum
