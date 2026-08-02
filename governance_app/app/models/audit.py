from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.job import JSON_TYPE, UUID_TYPE, utcnow


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_object", "object_type", "object_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[str] = mapped_column(String(255), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    details: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class PolicyReconciliation(Base):
    __tablename__ = "policy_reconciliations"

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    policy_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source_version: Mapped[str] = mapped_column(String(128), nullable=False)
    desired_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_hash: Mapped[str | None] = mapped_column(String(64))
    ranger_policy_id: Mapped[str | None] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    result: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class AccessVerification(Base):
    __tablename__ = "access_verifications"

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    policy_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    verification_group_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    identity: Mapped[str] = mapped_column(String(255), nullable=False)
    expected_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    observed_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    query_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    error_class: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
