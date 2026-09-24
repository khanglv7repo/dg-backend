from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.job import JSON_TYPE, UUID_TYPE, utcnow


class TestCaseRegistry(Base):
    """Backend DQ governance + OpenMetadata materialization registry.\n\n    lifecycle_state tracks governance intent:\n        STAGED -> APPROVED -> EXECUTABLE | FAILED\n\n    reservation_state tracks OM materialization:\n        NOT_STARTED -> RESERVED -> CONFIRMED | FAILED\n\n    OpenMetadata remains authoritative for the materialized TestCase itself.\n    natural_key_hash is the deterministic TestCase name/idempotency key.\n    """

    __tablename__ = "testcase_registry"
    __table_args__ = (
        UniqueConstraint("natural_key_hash", name="uq_testcase_registry_natural_key_hash"),
        Index("ix_testcase_registry_state", "reservation_state", "reserved_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    natural_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    target_entity_fqn: Mapped[str] = mapped_column(String(1024), nullable=False)
    test_definition_fqn: Mapped[str] = mapped_column(String(512), nullable=False)
    stable_test_slot_id: Mapped[str] = mapped_column(String(512), nullable=False)
    om_testcase_id: Mapped[str | None] = mapped_column(String(64))
    reservation_state: Mapped[str] = mapped_column(String(32), nullable=False, default="NOT_STARTED")
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_id: Mapped[str] = mapped_column(String(128), nullable=False)
    spec_payload: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    om_testcase_fqn: Mapped[str | None] = mapped_column(String(3072))
    lifecycle_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="STAGED"
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(255))
