"""phase1 completion tables

Revision ID: 0005_phase1_completion
Revises: 0004_single_app
Create Date: 2026-07-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_phase1_completion"
down_revision: Union[str, None] = "0004_single_app"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "data_value_scan_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_fqn", sa.String(length=1024), nullable=False),
        sa.Column("field_path", sa.String(length=1024), nullable=True),
        sa.Column("scanner_version", sa.String(length=255), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("total_samples", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("matched_samples", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("metrics", JSON_TYPE, nullable=False),
        sa.Column("suggestions", JSON_TYPE, nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="COMPLETED"),
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_data_value_scan_runs_entity",
        "data_value_scan_runs",
        ["entity_fqn", "created_at"],
        unique=False,
    )

    op.create_table(
        "integration_watermarks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("system_name", sa.String(length=64), nullable=False),
        sa.Column("watermark_key", sa.String(length=128), nullable=False),
        sa.Column("watermark_value", sa.String(length=255), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("system_name", "watermark_key", name="uq_integration_watermarks_key"),
    )


def downgrade() -> None:
    op.drop_table("integration_watermarks")
    op.drop_index("ix_data_value_scan_runs_entity", table_name="data_value_scan_runs")
    op.drop_table("data_value_scan_runs")
