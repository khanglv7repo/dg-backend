"""Transaction and durability unit tests for OpenMetadata event intake and Celery task publishing."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from sqlalchemy.orm import Session as SQLAlchemySession

from app.core.config import Settings
from app.models.event_inbox import EventInbox
from app.services.openmetadata_event_adapter import OpenMetadataEventAdapterService


def _settings() -> Settings:
    return Settings(_env_file=None)


def test_inbox_is_visible_from_independent_session_before_celery_publish(session) -> None:
    """Prove that EventInbox record is COMMITTED in TX1 and visible to an independent Session B BEFORE Celery publish."""
    raw_event = {
        "id": "evt-tx1-visible-001",
        "eventType": "entityCreated",
        "entityType": "table",
        "entityFullyQualifiedName": "trino_catalog.sales.orders",
        "timestamp": 1722240200000,
    }

    inbox_visible_in_session_b = False

    def spy_classify_delay(*args, **kwargs):
        nonlocal inbox_visible_in_session_b
        # Session B using the active test connection to query committed DB state
        session_b = SQLAlchemySession(bind=session.get_bind())
        try:
            found = (
                session_b.query(EventInbox)
                .filter(EventInbox.event_id == "evt-tx1-visible-001")
                .first()
            )
            if found is not None:
                inbox_visible_in_session_b = True
        finally:
            session_b.close()

        mock_task = MagicMock()
        mock_task.id = "task-classify-spy-1"
        return mock_task

    with patch("app.services.openmetadata_event_adapter.classify_entity") as mock_classify, \
         patch("app.services.openmetadata_event_adapter.sync_tags_to_ranger") as mock_tag_sync:

        mock_classify.delay.side_effect = spy_classify_delay
        mock_tag_sync.delay.return_value.id = "task-sync-spy-1"

        adapter = OpenMetadataEventAdapterService(session, _settings())
        res = adapter.process_change_event(raw_event)

        assert res["status"] == "accepted"
        assert inbox_visible_in_session_b is True, (
            "TX1 requirement: EventInbox row MUST be committed and readable by an independent Session B "
            "at the exact moment Celery task delay() is called!"
        )


def test_failed_publish_remains_dispatchable(session) -> None:
    """Simulate Celery task publication failure.

    Required:
    - Inbox record remains durable in DB with status RECEIVED/DISPATCH_PENDING;
    - Event is NOT marked PROCESSED;
    - Future delivery/recovery attempt can publish missing tasks.
    """
    raw_event = {
        "id": "evt-failed-celery-002",
        "eventType": "entityFieldsChanged",
        "entityType": "table",
        "entityFullyQualifiedName": "trino_catalog.sales.orders",
        "changeDescription": {
            "fieldsAdded": [{"name": "columns.price.tags", "newValue": "PII.Price"}]
        },
    }

    with patch("app.services.openmetadata_event_adapter.sync_tags_to_ranger") as mock_tag_sync:
        mock_tag_sync.delay.side_effect = RuntimeError("Broker connection lost")

        adapter = OpenMetadataEventAdapterService(session, _settings())
        res = adapter.process_change_event(raw_event)

        assert res["status"] == "accepted"

        record = (
            session.query(EventInbox)
            .filter(EventInbox.event_id == "evt-failed-celery-002")
            .first()
        )
        assert record is not None, "Inbox record MUST remain durable despite Celery publish failure!"
        assert record.status != "PROCESSED", "Event MUST NOT be marked PROCESSED when Celery publish fails!"
        assert record.status in ("RECEIVED", "DISPATCH_PENDING")


def test_duplicate_pending_event_retries_missing_dispatch(session) -> None:
    """Test duplicate webhook delivery when previous publish failed: retries missing task dispatch."""
    raw_event = {
        "id": "evt-retry-003",
        "eventType": "entityCreated",
        "entityType": "table",
        "entityFullyQualifiedName": "trino_catalog.sales.orders",
    }

    with patch("app.services.openmetadata_event_adapter.classify_entity") as mock_classify, \
         patch("app.services.openmetadata_event_adapter.sync_tags_to_ranger") as mock_tag_sync:

        mock_classify.delay.side_effect = RuntimeError("Broker unreachable")
        mock_tag_sync.delay.side_effect = RuntimeError("Broker unreachable")

        adapter = OpenMetadataEventAdapterService(session, _settings())
        res1 = adapter.process_change_event(raw_event)
        assert res1["dispatched_tasks"] == []

        mock_classify.delay.side_effect = None
        mock_tag_sync.delay.side_effect = None
        mock_classify.delay.return_value.id = "task-c-retry"
        mock_tag_sync.delay.return_value.id = "task-t-retry"

        res2 = adapter.process_change_event(raw_event)
        assert res2["status"] == "accepted"
        assert len(res2["dispatched_tasks"]) == 2

        rec = session.query(EventInbox).filter(EventInbox.event_id == "evt-retry-003").first()
        assert rec.status == "PROCESSED"


def test_partial_dispatch_does_not_republish_successful_purpose(session) -> None:
    """Test partial dispatch failure: CLASSIFY succeeds, TAG_SYNC fails.

    Subsequent retry publishes ONLY TAG_SYNC without re-publishing CLASSIFY.
    """
    raw_event = {
        "id": "evt-partial-004",
        "eventType": "entityCreated",
        "entityType": "table",
        "entityFullyQualifiedName": "trino_catalog.sales.orders",
    }

    with patch("app.services.openmetadata_event_adapter.classify_entity") as mock_classify, \
         patch("app.services.openmetadata_event_adapter.sync_tags_to_ranger") as mock_tag_sync:

        mock_classify.delay.return_value.id = "task-c-partial-1"
        mock_tag_sync.delay.side_effect = RuntimeError("Ranger worker down")

        adapter = OpenMetadataEventAdapterService(session, _settings())
        res1 = adapter.process_change_event(raw_event)
        assert res1["dispatched_tasks"] == ["task-c-partial-1"]
        assert mock_classify.delay.call_count == 1
        assert mock_tag_sync.delay.call_count == 1

        mock_tag_sync.delay.side_effect = None
        mock_tag_sync.delay.return_value.id = "task-t-partial-2"

        res2 = adapter.process_change_event(raw_event)
        assert mock_classify.delay.call_count == 1, "CLASSIFY must NOT be re-published!"
        assert mock_tag_sync.delay.call_count == 2
        assert res2["dispatched_tasks"] == ["task-t-partial-2"]

        rec = session.query(EventInbox).filter(EventInbox.event_id == "evt-partial-004").first()
        assert rec.status == "PROCESSED"
        assert set(rec.dispatched_purposes) == {"CLASSIFY", "TAG_SYNC"}


def test_openmetadata_route_does_not_wrap_service_owned_transactions() -> None:
    route_source = (
        __import__(
            "pathlib"
        ).Path("app/api/routes/openmetadata_events.py").read_text()
    )
    route_body = route_source.split("def accept_openmetadata_event", 1)[1].split(
        "@router.post",
        1,
    )[0]

    assert "with db.begin()" not in route_body
    assert "adapter.process_change_event" in route_body
