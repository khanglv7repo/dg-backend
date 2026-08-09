"""R4 logical data-access policy versions and Ranger projections

Revision ID: 0011_r4_data_access_policy
Revises: 0010_r3_final_correctness
Create Date: 2026-08-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0011_r4_data_access_policy"
down_revision: Union[str, None] = "0010_r3_final_correctness"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "data_access_policy_version",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("policy_key", sa.String(length=512), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("logical_policy", JSON_TYPE, nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'ACTIVE', 'INACTIVE')",
            name="ck_data_access_policy_version_status",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_data_access_policy_version"),
        sa.UniqueConstraint(
            "policy_key",
            "version",
            name="uq_data_access_policy_version_key_version",
        ),
    )
    op.create_index(
        "ix_data_access_policy_version_key_status",
        "data_access_policy_version",
        ["policy_key", "status"],
        unique=False,
    )
    op.create_index(
        "uq_data_access_policy_version_one_active",
        "data_access_policy_version",
        ["policy_key"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
        sqlite_where=sa.text("status = 'ACTIVE'"),
    )

    op.create_table(
        "ranger_policy_projection",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("policy_version_id", sa.Uuid(), nullable=False),
        sa.Column("projection_type", sa.String(length=32), nullable=False),
        sa.Column("projection_key", sa.String(length=128), nullable=False),
        sa.Column("ranger_service", sa.String(length=255), nullable=False),
        sa.Column("ranger_policy_name", sa.String(length=255), nullable=False),
        sa.Column("ranger_policy_id", sa.String(length=64), nullable=True),
        sa.Column("ranger_policy_guid", sa.String(length=128), nullable=True),
        sa.Column("desired_checksum", sa.String(length=64), nullable=False),
        sa.Column("observed_checksum", sa.String(length=64), nullable=True),
        sa.Column("sync_status", sa.String(length=32), nullable=False),
        sa.Column("reconciliation_details", JSON_TYPE, nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "projection_type IN ('ACCESS', 'MASK', 'ROW_FILTER')",
            name="ck_ranger_policy_projection_type",
        ),
        sa.ForeignKeyConstraint(
            ["policy_version_id"],
            ["data_access_policy_version.id"],
            name="fk_ranger_policy_projection_policy_version",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ranger_policy_projection"),
        sa.UniqueConstraint(
            "policy_version_id",
            "ranger_policy_name",
            name="uq_ranger_policy_projection_version_name",
        ),
    )
    op.create_index(
        "ix_ranger_policy_projection_version_status",
        "ranger_policy_projection",
        ["policy_version_id", "sync_status"],
        unique=False,
    )
    op.create_index(
        "ix_ranger_policy_projection_name",
        "ranger_policy_projection",
        ["ranger_service", "ranger_policy_name"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ranger_policy_projection_name",
        table_name="ranger_policy_projection",
    )
    op.drop_index(
        "ix_ranger_policy_projection_version_status",
        table_name="ranger_policy_projection",
    )
    op.drop_table("ranger_policy_projection")

    op.drop_index(
        "uq_data_access_policy_version_one_active",
        table_name="data_access_policy_version",
    )
    op.drop_index(
        "ix_data_access_policy_version_key_status",
        table_name="data_access_policy_version",
    )
    op.drop_table("data_access_policy_version")
