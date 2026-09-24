"""DQ approval lifecycle state

Revision ID: 0014_dq_approval_lifecycle
Revises: 0013_r8_outbox_and_dq_registry
Create Date: 2026-09-24

Adds an explicit human/operator approval state without making TestCases
executable. Existing registry rows are backfilled as STAGED.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0014_dq_approval_lifecycle"
down_revision: Union[str, None] = "0013_r8_outbox_and_dq_registry"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "testcase_registry",
        sa.Column(
            "lifecycle_state",
            sa.String(length=32),
            nullable=False,
            server_default="STAGED",
        ),
    )
    op.add_column(
        "testcase_registry",
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "testcase_registry",
        sa.Column("approved_by", sa.String(length=255), nullable=True),
    )
    op.alter_column("testcase_registry", "lifecycle_state", server_default=None)


def downgrade() -> None:
    op.drop_column("testcase_registry", "approved_by")
    op.drop_column("testcase_registry", "approved_at")
    op.drop_column("testcase_registry", "lifecycle_state")
