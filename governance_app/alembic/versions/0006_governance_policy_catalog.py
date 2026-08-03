"""PostgreSQL desired-state Ranger policy catalog

Revision ID: 0006_governance_policy_catalog
Revises: 0005_phase1_completion
Create Date: 2026-08-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_governance_policy_catalog"
down_revision: Union[str, None] = "0005_phase1_completion"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "governance_policies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("policy_key", sa.String(length=512), nullable=False),
        sa.Column("policy_kind", sa.String(length=32), nullable=False),
        sa.Column("service", sa.String(length=255), nullable=False),
        sa.Column("service_type", sa.String(length=255), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("document", JSON_TYPE, nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("policy_key", name="uq_governance_policies_policy_key"),
        sa.UniqueConstraint(
            "service",
            "name",
            name="uq_governance_policies_service_name",
        ),
    )
    op.create_index(
        "ix_governance_policies_service_enabled",
        "governance_policies",
        ["service", "enabled"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_governance_policies_service_enabled",
        table_name="governance_policies",
    )
    op.drop_table("governance_policies")
