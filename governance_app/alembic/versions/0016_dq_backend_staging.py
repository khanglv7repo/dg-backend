"""Backend-only DQ staging payload

Revision ID: 0016_dq_backend_staging
Revises: 0015_trino_runtime_verification
Create Date: 2026-09-24

A TestCase created in OpenMetadata 2.0.2 is automatically attached to the
table's Basic TestSuite. Therefore STAGED must exist only in Backend before
OpenMetadata materialization.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0016_dq_backend_staging"
down_revision: Union[str, None] = "0015_trino_runtime_verification"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "testcase_registry",
        sa.Column(
            "spec_payload",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.add_column(
        "testcase_registry",
        sa.Column("om_testcase_fqn", sa.String(length=3072), nullable=True),
    )
    op.alter_column("testcase_registry", "spec_payload", server_default=None)

    # Existing rows were created under the old direct-create flow. If OM
    # confirmation already exists, preserve that fact as EXECUTABLE.
    op.execute(
        """
        UPDATE testcase_registry
        SET lifecycle_state = 'EXECUTABLE'
        WHERE reservation_state = 'CONFIRMED'
          AND om_testcase_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("testcase_registry", "om_testcase_fqn")
    op.drop_column("testcase_registry", "spec_payload")
