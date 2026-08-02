"""remove legacy in-process AI_CLASSIFY job contract

Revision ID: 0003_agent_boundary
Revises: 0002_om_native
Create Date: 2026-07-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_agent_boundary"
down_revision: Union[str, None] = "0002_om_native"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Do not strand active in-process LLM jobs during the component split."""

    bind = op.get_bind()
    active = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM governance_jobs "
            "WHERE job_type = 'AI_CLASSIFY' "
            "AND status IN ('QUEUED', 'RUNNING', 'RETRY_WAIT')"
        )
    ).scalar_one()
    if active:
        raise RuntimeError(
            "Migration 0003 found active AI_CLASSIFY jobs. Resolve or cancel them before "
            "upgrading; LLM orchestration now belongs to the dedicated Agent runtime role."
        )


def downgrade() -> None:
    # No schema change. Downgrading only changes the code boundary.
    pass
