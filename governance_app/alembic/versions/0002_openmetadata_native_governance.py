"""replace backend proposals with OpenMetadata-native governance

Revision ID: 0002_om_native
Revises: 0001_initial
Create Date: 2026-07-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002_om_native"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _require_empty(table_name: str) -> None:
    bind = op.get_bind()
    count = bind.execute(sa.text(f"SELECT COUNT(*) FROM {table_name}")).scalar_one()
    if count:
        raise RuntimeError(
            f"Migration 0002 requires {table_name} to be empty; export or resolve the "
            "prototype records before upgrading because OpenMetadata now owns that state."
        )


def upgrade() -> None:
    # Do not silently discard prototype review or policy-authoring state.
    _require_empty("governance_proposals")
    _require_empty("policy_definition_versions")
    _require_empty("policy_definitions")

    op.create_table(
        "classification_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_fqn", sa.String(length=1024), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("source_version", sa.String(length=255), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("suggestions", JSON_TYPE, nullable=False),
        sa.Column("evidence", JSON_TYPE, nullable=False),
        sa.Column("openmetadata_suggestion_ids", JSON_TYPE, nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_id", "source_kind", "source_version", name="uq_classification_run_source"
        ),
    )
    op.create_index(
        "ix_classification_runs_entity_created",
        "classification_runs",
        ["entity_fqn", "created_at"],
        unique=False,
    )

    # Preserve already queued execution work under the new explicit names.
    op.execute(
        sa.text(
            "UPDATE governance_jobs SET job_type = CASE job_type "
            "WHEN 'APPLY_TAGS' THEN 'APPLY_CONFIRMED_TAGS' "
            "WHEN 'SYNC_POLICY' THEN 'RECONCILE_RANGER' "
            "WHEN 'VERIFY_ACCESS' THEN 'VERIFY_TRINO' "
            "ELSE job_type END"
        )
    )

    op.drop_index("ix_policy_definition_versions_active", table_name="policy_definition_versions")
    op.drop_table("policy_definition_versions")
    op.drop_table("policy_definitions")
    op.drop_index("ix_governance_proposals_status_created", table_name="governance_proposals")
    op.drop_table("governance_proposals")


def downgrade() -> None:
    op.create_table(
        "governance_proposals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("proposal_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("proposer_id", sa.String(length=255), nullable=False),
        sa.Column("proposer_name", sa.String(length=255), nullable=False),
        sa.Column("reviewer_id", sa.String(length=255), nullable=True),
        sa.Column("reviewer_name", sa.String(length=255), nullable=True),
        sa.Column("target_entity_type", sa.String(length=64), nullable=False),
        sa.Column("target_fqn", sa.String(length=1024), nullable=False),
        sa.Column("field_path", sa.String(length=1024), nullable=True),
        sa.Column("requested_change", JSON_TYPE, nullable=False),
        sa.Column("evidence", JSON_TYPE, nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("source_version", sa.String(length=128), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_governance_proposals_status_created",
        "governance_proposals",
        ["status", "created_at"],
        unique=False,
    )

    op.create_table(
        "policy_definitions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("policy_key", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("active_version", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("policy_key"),
    )
    op.create_table(
        "policy_definition_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("definition_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("document", JSON_TYPE, nullable=False),
        sa.Column("document_hash", sa.String(length=64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("approved_proposal_id", sa.String(length=36), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["definition_id"], ["policy_definitions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "definition_id", "version", name="uq_policy_definition_version"
        ),
    )
    op.create_index(
        "ix_policy_definition_versions_active",
        "policy_definition_versions",
        ["definition_id", "is_active"],
        unique=False,
    )

    op.execute(
        sa.text(
            "UPDATE governance_jobs SET job_type = CASE job_type "
            "WHEN 'APPLY_CONFIRMED_TAGS' THEN 'APPLY_TAGS' "
            "WHEN 'RECONCILE_RANGER' THEN 'SYNC_POLICY' "
            "WHEN 'VERIFY_TRINO' THEN 'VERIFY_ACCESS' "
            "ELSE job_type END"
        )
    )
    op.drop_index("ix_classification_runs_entity_created", table_name="classification_runs")
    op.drop_table("classification_runs")
