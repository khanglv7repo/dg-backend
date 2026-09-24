"""R8 event outbox and DQ testcase registry

Revision ID: 0013_r8_outbox_and_dq_registry
Revises: 0012_r5_service_mapping
Create Date: 2026-09-24

Additive only. See docs/13_IMPLEMENTATION_SPEC.md section 3.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0013_r8_outbox_and_dq_registry"
down_revision: Union[str, None] = "0012_r5_service_mapping"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "event_outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatch_attempts", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_event_outbox"),
    )
    op.create_index(
        "ix_event_outbox_status",
        "event_outbox",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_event_outbox_aggregate",
        "event_outbox",
        ["aggregate_type", "aggregate_id"],
        unique=False,
    )

    op.create_table(
        "testcase_registry",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("natural_key_hash", sa.String(length=64), nullable=False),
        sa.Column("target_entity_fqn", sa.String(length=1024), nullable=False),
        sa.Column("test_definition_fqn", sa.String(length=512), nullable=False),
        sa.Column("stable_test_slot_id", sa.String(length=512), nullable=False),
        sa.Column("om_testcase_id", sa.String(length=64), nullable=True),
        sa.Column("reservation_state", sa.String(length=32), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_testcase_registry"),
        sa.UniqueConstraint(
            "natural_key_hash",
            name="uq_testcase_registry_natural_key_hash",
        ),
    )
    op.create_index(
        "ix_testcase_registry_state",
        "testcase_registry",
        ["reservation_state", "reserved_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_testcase_registry_state", table_name="testcase_registry")
    op.drop_table("testcase_registry")
    op.drop_index("ix_event_outbox_aggregate", table_name="event_outbox")
    op.drop_index("ix_event_outbox_status", table_name="event_outbox")
    op.drop_table("event_outbox")
