"""R3 final correctness idempotency constraints

Revision ID: 0010_r3_final_correctness
Revises: 0009_r3_tag_correctness
Create Date: 2026-08-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0010_r3_final_correctness"
down_revision: Union[str, None] = "0009_r3_tag_correctness"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "classification_executions" not in tables:
        return

    columns = {
        column["name"]
        for column in inspector.get_columns("classification_executions")
    }
    if "idempotency_key" not in columns:
        op.add_column(
            "classification_executions",
            sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        )

    constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("classification_executions")
    }
    if "uq_classification_execution_idempotency_key" in constraints:
        return

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("classification_executions") as batch_op:
            batch_op.create_unique_constraint(
                "uq_classification_execution_idempotency_key",
                ["idempotency_key"],
            )
    else:
        op.create_unique_constraint(
            "uq_classification_execution_idempotency_key",
            "classification_executions",
            ["idempotency_key"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "classification_executions" not in tables:
        return

    constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("classification_executions")
    }
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("classification_executions") as batch_op:
            if "uq_classification_execution_idempotency_key" in constraints:
                batch_op.drop_constraint(
                    "uq_classification_execution_idempotency_key",
                    type_="unique",
                )
            columns = {
                column["name"]
                for column in inspector.get_columns("classification_executions")
            }
            if "idempotency_key" in columns:
                batch_op.drop_column("idempotency_key")
    else:
        if "uq_classification_execution_idempotency_key" in constraints:
            op.drop_constraint(
                "uq_classification_execution_idempotency_key",
                "classification_executions",
                type_="unique",
            )
        columns = {
            column["name"]
            for column in inspector.get_columns("classification_executions")
        }
        if "idempotency_key" in columns:
            op.drop_column("classification_executions", "idempotency_key")
