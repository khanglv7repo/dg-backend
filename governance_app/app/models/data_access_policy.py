from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    JSON,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.job import UUID_TYPE, utcnow


# IMPORTANT: keep R4 MutableDict instrumentation isolated from the shared
# app.models.job.JSON_TYPE singleton. Legacy R1-R3 models store both dicts
# and lists in that shared type; applying R4_MUTABLE_DICT_JSON_TYPE
# would instrument those legacy list-valued attributes as MutableDict and
# reject values such as suggestions=[] / purposes=[...].
R4_MUTABLE_DICT_JSON_TYPE = MutableDict.as_mutable(
    JSON().with_variant(JSONB, "postgresql")
)


class DataAccessPolicyVersion(Base):
    __tablename__ = "data_access_policy_version"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT', 'ACTIVE', 'INACTIVE')",
            name="ck_data_access_policy_version_status",
        ),
        UniqueConstraint(
            "policy_key",
            "version",
            name="uq_data_access_policy_version_key_version",
        ),
        Index(
            "uq_data_access_policy_version_one_active",
            "policy_key",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
            sqlite_where=text("status = 'ACTIVE'"),
        ),
        Index(
            "ix_data_access_policy_version_key_status",
            "policy_key",
            "status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        primary_key=True,
        default=uuid.uuid4,
    )
    policy_key: Mapped[str] = mapped_column(String(512), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    logical_policy: Mapped[dict] = mapped_column(
        R4_MUTABLE_DICT_JSON_TYPE,
        nullable=False,
    )
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


@event.listens_for(DataAccessPolicyVersion, "before_update")
def _prevent_policy_content_mutation(_mapper, _connection, target) -> None:
    """Allow lifecycle fields to change, but keep version content immutable."""

    state = inspect(target)
    immutable_fields = ("policy_key", "version", "logical_policy", "checksum", "created_by")
    changed = [
        field
        for field in immutable_fields
        if state.attrs[field].history.has_changes()
    ]
    if changed:
        raise ValueError(
            "data-access policy version content is immutable: "
            + ", ".join(sorted(changed))
        )


class RangerPolicyProjection(Base):
    __tablename__ = "ranger_policy_projection"
    __table_args__ = (
        CheckConstraint(
            "projection_type IN ('ACCESS', 'MASK', 'ROW_FILTER')",
            name="ck_ranger_policy_projection_type",
        ),
        UniqueConstraint(
            "policy_version_id",
            "ranger_policy_name",
            name="uq_ranger_policy_projection_version_name",
        ),
        Index(
            "ix_ranger_policy_projection_version_status",
            "policy_version_id",
            "sync_status",
        ),
        Index(
            "ix_ranger_policy_projection_name",
            "ranger_service",
            "ranger_policy_name",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        primary_key=True,
        default=uuid.uuid4,
    )
    policy_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("data_access_policy_version.id", ondelete="CASCADE"),
        nullable=False,
    )
    projection_type: Mapped[str] = mapped_column(String(32), nullable=False)
    projection_key: Mapped[str] = mapped_column(String(128), nullable=False)
    ranger_service: Mapped[str] = mapped_column(String(255), nullable=False)
    ranger_policy_name: Mapped[str] = mapped_column(String(255), nullable=False)
    ranger_policy_id: Mapped[str | None] = mapped_column(String(64))
    ranger_policy_guid: Mapped[str | None] = mapped_column(String(128))
    desired_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_checksum: Mapped[str | None] = mapped_column(String(64))
    sync_status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    reconciliation_details: Mapped[dict] = mapped_column(
        R4_MUTABLE_DICT_JSON_TYPE,
        nullable=False,
        default=dict,
    )
    last_error: Mapped[str | None] = mapped_column(Text)
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
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
