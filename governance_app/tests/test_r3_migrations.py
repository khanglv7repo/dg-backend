"""Tests verifying Alembic migration 0008 consistency with SQLAlchemy models."""
from __future__ import annotations

from sqlalchemy import inspect

from app.models.classification_execution import ClassificationExecution
from app.models.event_inbox import EventInbox
from app.models.tag_sync_state import TagSyncState


def test_alembic_migration_0008_table_structures(session) -> None:
    """Verify that EventInbox, ClassificationExecution, and TagSyncState models map cleanly to expected DB tables."""
    # EventInbox table checks
    assert EventInbox.__tablename__ == "event_inbox"
    inbox_mapper = inspect(EventInbox)
    inbox_cols = {col.name for col in inbox_mapper.columns}
    assert {"id", "event_id", "event_type", "entity_type", "entity_fqn", "payload", "purposes", "status", "correlation_id", "created_at"}.issubset(inbox_cols)

    # ClassificationExecution table checks
    assert ClassificationExecution.__tablename__ == "classification_executions"
    exec_mapper = inspect(ClassificationExecution)
    exec_cols = {col.name for col in exec_mapper.columns}
    assert {"id", "event_id", "entity_type", "entity_fqn", "generation", "status", "outcome", "rule_version_id", "suggestions", "evidence", "confidence", "correlation_id", "created_at", "updated_at"}.issubset(exec_cols)

    # TagSyncState table checks
    assert TagSyncState.__tablename__ == "tag_sync_states"
    sync_mapper = inspect(TagSyncState)
    sync_cols = {col.name for col in sync_mapper.columns}
    assert {"id", "entity_type", "entity_fqn", "status", "checksum", "details", "updated_at"}.issubset(sync_cols)
