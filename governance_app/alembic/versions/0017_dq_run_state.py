"""DQ run coordination and result state

Revision ID: 0017_dq_run_state
Revises: 0016_dq_backend_staging
Create Date: 2026-09-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0017_dq_run_state"
down_revision: Union[str, None] = "0016_dq_backend_staging"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "testcase_registry",
        sa.Column("om_test_suite_fqn", sa.String(length=3072), nullable=True),
    )
    op.add_column(
        "testcase_registry",
        sa.Column(
            "run_generation",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "testcase_registry",
        sa.Column("active_run_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "testcase_registry",
        sa.Column("last_run_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "testcase_registry",
        sa.Column("last_run_queued_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "testcase_registry",
        sa.Column("last_run_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "testcase_registry",
        sa.Column("last_run_finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "testcase_registry",
        sa.Column("last_run_requested_by", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "testcase_registry",
        sa.Column("last_run_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "testcase_registry",
        sa.Column(
            "last_result",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.alter_column("testcase_registry", "run_generation", server_default=None)
    op.alter_column("testcase_registry", "last_result", server_default=None)


def downgrade() -> None:
    op.drop_column("testcase_registry", "last_result")
    op.drop_column("testcase_registry", "last_run_error")
    op.drop_column("testcase_registry", "last_run_requested_by")
    op.drop_column("testcase_registry", "last_run_finished_at")
    op.drop_column("testcase_registry", "last_run_started_at")
    op.drop_column("testcase_registry", "last_run_queued_at")
    op.drop_column("testcase_registry", "last_run_status")
    op.drop_column("testcase_registry", "active_run_id")
    op.drop_column("testcase_registry", "run_generation")
    op.drop_column("testcase_registry", "om_test_suite_fqn")
