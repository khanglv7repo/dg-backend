"""DB-backed JSON classification rule sets

Revision ID: 0007_classification_rule_sets
Revises: 0006_governance_policy_catalog
Create Date: 2026-08-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_classification_rule_sets"
down_revision: Union[str, None] = (
    "0006_governance_policy_catalog"
)
branch_labels: Union[
    str,
    Sequence[str],
    None,
] = None
depends_on: Union[
    str,
    Sequence[str],
    None,
] = None

JSON_TYPE = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()),
    "postgresql",
)


def upgrade() -> None:
    op.create_table(
        "classification_rule_sets",
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
        ),
        sa.Column(
            "name",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "declared_version",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "document",
            JSON_TYPE,
            nullable=False,
        ),
        sa.Column(
            "document_sha256",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="INACTIVE",
        ),
        sa.Column(
            "created_by",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "created_by_name",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "activated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "name",
            "document_sha256",
            name=(
                "uq_classification_rule_sets_name_sha256"
            ),
        ),
    )
    op.create_index(
        "ix_classification_rule_sets_name_status",
        "classification_rule_sets",
        ["name", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_classification_rule_sets_name_status",
        table_name="classification_rule_sets",
    )
    op.drop_table(
        "classification_rule_sets"
    )
