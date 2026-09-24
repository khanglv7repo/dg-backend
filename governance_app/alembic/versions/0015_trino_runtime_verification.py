"""Separate Trino runtime verification state

Revision ID: 0015_trino_runtime_verification
Revises: 0014_dq_approval_lifecycle
Create Date: 2026-09-24

Ranger convergence and Trino runtime verification are different state
machines. Existing projections are backfilled as UNVERIFIED.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0015_trino_runtime_verification"
down_revision: Union[str, None] = "0014_dq_approval_lifecycle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ranger_policy_projection",
        sa.Column(
            "verification_status",
            sa.String(length=32),
            nullable=False,
            server_default="UNVERIFIED",
        ),
    )
    op.add_column(
        "ranger_policy_projection",
        sa.Column(
            "verification_details",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.add_column(
        "ranger_policy_projection",
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.alter_column(
        "ranger_policy_projection", "verification_status", server_default=None
    )
    op.alter_column(
        "ranger_policy_projection", "verification_details", server_default=None
    )


def downgrade() -> None:
    op.drop_column("ranger_policy_projection", "last_verified_at")
    op.drop_column("ranger_policy_projection", "verification_details")
    op.drop_column("ranger_policy_projection", "verification_status")
