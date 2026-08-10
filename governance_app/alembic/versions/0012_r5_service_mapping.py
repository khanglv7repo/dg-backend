"""R5 service mapping source of truth

Revision ID: 0012_r5_service_mapping
Revises: 0011_r4_data_access_policy
Create Date: 2026-08-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0012_r5_service_mapping"
down_revision: Union[str, None] = "0011_r4_data_access_policy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "service_mapping",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("om_service_name", sa.String(length=255), nullable=False),
        sa.Column("trino_catalog", sa.String(length=255), nullable=False),
        sa.Column("ranger_service_name", sa.String(length=255), nullable=False),
        sa.Column("ranger_tag_service_name", sa.String(length=255), nullable=True),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_service_mapping"),
        sa.UniqueConstraint(
            "om_service_name",
            "environment",
            name="uq_service_mapping_om_service_environment",
        ),
    )
    op.create_index(
        "ix_service_mapping_trino_catalog",
        "service_mapping",
        ["trino_catalog"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_service_mapping_trino_catalog", table_name="service_mapping")
    op.drop_table("service_mapping")
