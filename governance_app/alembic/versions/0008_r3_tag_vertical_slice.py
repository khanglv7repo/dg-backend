"""R3 TAG vertical slice tables: event_inbox, classification_executions, tag_sync_states

Revision ID: 0008_r3_tag_vertical_slice
Revises: 0007_classification_rule_sets
Create Date: 2026-08-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0008_r3_tag_vertical_slice'
down_revision: Union[str, None] = '0007_classification_rule_sets'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'event_inbox',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('event_id', sa.String(length=255), nullable=False),
        sa.Column('event_type', sa.String(length=64), nullable=False),
        sa.Column('entity_type', sa.String(length=64), nullable=False),
        sa.Column('entity_fqn', sa.String(length=1024), nullable=False),
        sa.Column('payload', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
        sa.Column('purposes', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('correlation_id', sa.String(length=128), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_id', name='uq_event_inbox_event_id')
    )
    op.create_index('ix_event_inbox_entity', 'event_inbox', ['entity_type', 'entity_fqn', 'created_at'], unique=False)
    op.create_index('ix_event_inbox_status', 'event_inbox', ['status', 'created_at'], unique=False)

    op.create_table(
        'classification_executions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('event_id', sa.String(length=255), nullable=False),
        sa.Column('entity_type', sa.String(length=64), nullable=False),
        sa.Column('entity_fqn', sa.String(length=1024), nullable=False),
        sa.Column('generation', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('outcome', sa.String(length=32), nullable=True),
        sa.Column('rule_version_id', sa.String(length=255), nullable=True),
        sa.Column('suggestions', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
        sa.Column('evidence', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('correlation_id', sa.String(length=128), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_id', 'entity_fqn', 'generation', name='uq_classification_execution_gen')
    )
    op.create_index('ix_classification_executions_entity', 'classification_executions', ['entity_fqn', 'created_at'], unique=False)
    op.create_index('ix_classification_executions_status', 'classification_executions', ['status', 'created_at'], unique=False)

    op.create_table(
        'tag_sync_states',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('entity_type', sa.String(length=64), nullable=False),
        sa.Column('entity_fqn', sa.String(length=1024), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('checksum', sa.String(length=64), nullable=True),
        sa.Column('details', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('entity_type', 'entity_fqn', name='uq_tag_sync_states_entity')
    )
    op.create_index('ix_tag_sync_states_status', 'tag_sync_states', ['status', 'updated_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_tag_sync_states_status', table_name='tag_sync_states')
    op.drop_table('tag_sync_states')
    op.drop_index('ix_classification_executions_status', table_name='classification_executions')
    op.drop_index('ix_classification_executions_entity', table_name='classification_executions')
    op.drop_table('classification_executions')
    op.drop_index('ix_event_inbox_status', table_name='event_inbox')
    op.drop_index('ix_event_inbox_entity', table_name='event_inbox')
    op.drop_table('event_inbox')
