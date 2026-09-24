"""Transaction and durability tests for OpenMetadata webhook -> TAG_SYNC."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from sqlalchemy.orm import Session as SQLAlchemySession

from app.core.config import Settings
from app.models.event_inbox import EventInbox
from app.services.openmetadata_event_adapter import OpenMetadataEventAdapterService


def _settings() -> Settings:
    return Settings(_env_file=None)


def test_inbox_is_visible_before_tag_sync_publish(session) -> None:
    raw_event = {
        "id": "evt-tx1-visible-001",
        "eventType": "entityCreated",
        "entityType": "table",
        "entityFullyQualifiedName": "trino_catalog.sales.orders",
        "timestamp": 1722240200000,
    }
    visible = False

    def spy_delay(*, entity_type, entity_fqn, correlation_id):
        nonlocal visible
        session_b = SQLAlchemySession(bind=session.get_bind())
        try:
            visible = (
                session_b.query(EventInbox)
                .filter(EventInbox.event_id == "evt-tx1-visible-001")
                .first()
                is not None
            )
        finally:
            session_b.close()
        task = MagicMock()
        task.id = "task-sync-spy-1"
        return task

    with patch(
        "app.services.openmetadata_event_adapter.sync_tags_to_ranger"
    ) as sync:
        sync.delay.side_effect = spy_delay
        result = OpenMetadataEventAdapterService(
            session, _settings()
        ).process_change_event(raw_event)

    assert result["status"] == "accepted"
    assert visible is True
    assert result["dispatched_tasks"] == ["task-sync-spy-1"]


def test_failed_tag_sync_publish_remains_dispatchable(session) -> None:
    raw_event = {
        "id": "evt-failed-celery-002",
        "eventType": "entityFieldsChanged",
        "entityType": "table",
        "entityFullyQualifiedName": "trino_catalog.sales.orders",
        "changeDescription": {
            "fieldsAdded": [
                {"name": "columns.price.tags", "newValue": "PII.Price"}
            ]
        },
    }

    with patch(
        "app.services.openmetadata_event_adapter.sync_tags_to_ranger"
    ) as sync:
        sync.delay.side_effect = RuntimeError("Broker connection lost")
        result = OpenMetadataEventAdapterService(
            session, _settings()
        ).process_change_event(raw_event)

    assert result["status"] == "accepted"
    record = (
        session.query(EventInbox)
        .filter(EventInbox.event_id == "evt-failed-celery-002")
        .first()
    )
    assert record is not None
    assert record.status in ("RECEIVED", "DISPATCH_PENDING")
    assert record.status != "PROCESSED"


def test_duplicate_pending_event_retries_missing_tag_sync_dispatch(session) -> None:
    raw_event = {
        "id": "evt-retry-003",
        "eventType": "entityCreated",
        "entityType": "table",
        "entityFullyQualifiedName": "trino_catalog.sales.orders",
    }

    with patch(
        "app.services.openmetadata_event_adapter.sync_tags_to_ranger"
    ) as sync:
        sync.delay.side_effect = RuntimeError("Broker unreachable")
        adapter = OpenMetadataEventAdapterService(session, _settings())
        first = adapter.process_change_event(raw_event)
        assert first["dispatched_tasks"] == []

        task = MagicMock()
        task.id = "task-t-retry"
        sync.delay.side_effect = None
        sync.delay.return_value = task

        second = adapter.process_change_event(raw_event)

    assert second["status"] == "accepted"
    assert second["dispatched_tasks"] == ["task-t-retry"]
    record = (
        session.query(EventInbox)
        .filter(EventInbox.event_id == "evt-retry-003")
        .first()
    )
    assert record.status == "PROCESSED"
