"""merge agent runtime into one application with separate worker roles

Revision ID: 0004_single_app
Revises: 0003_agent_boundary
Create Date: 2026-07-29
"""
from typing import Sequence, Union

revision: str = "0004_single_app"
down_revision: Union[str, None] = "0003_agent_boundary"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No schema change. AGENT_CLASSIFY uses the existing governance_jobs.job_type column.
    pass


def downgrade() -> None:
    # No schema change. Downgrading restores the previous deployment topology only.
    pass
