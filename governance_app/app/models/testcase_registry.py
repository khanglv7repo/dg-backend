from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.job import UUID_TYPE, utcnow


class TestCaseRegistry(Base):
    """Backend-side coordination/crash-recovery for Agent DIRECT-CREATE DQ
    TestCase writes (I1). This registry is NOT the source of truth for
    TestCase existence -- OpenMetadata is. It exists only to serialize
    concurrent workers reserving the same natural_key_hash and to let a
    crash-recovery job reconcile against OM by deterministic FQN.

    natural_key_hash is the deterministic FQN value confirmed by B3:
    dg_<sha256(natural_key)[:32]>.
    """

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
    reservation_state: Mapped[str] = mapped_column(String(32), nullable=False, default="RESERVED")
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_id: Mapped[str] = mapped_column(String(128), nullable=False)
