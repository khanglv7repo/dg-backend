from __future__ import annotations

from unittest.mock import patch

from app.core.config import Settings
from app.services.event_router import EventPurpose
from app.services.openmetadata_event_adapter import OpenMetadataEventAdapterService


def _settings() -> Settings:
    return Settings(_env_file=None)


def test_tag_change_enqueues_tag_sync(session) -> None:
    event = {
        "id": "evt-confirm-email",
        "eventType": "ENTITY_UPDATED",
        "entityType": "table",
        "entityFullyQualifiedName": "hive.sales.customers",
        "timestamp": 123,
        "changeDescription": {
            "fieldsUpdated": [
                {
                    "name": "columns.email.tags",
                    "oldValue": [],
                    "newValue": [{"tagFQN": "PII.Email", "state": "Confirmed"}],
                }
            ]
        },
    }

    with patch("app.services.openmetadata_event_adapter.sync_tags_to_ranger") as mock_tag_sync:
        mock_tag_sync.delay.return_value.id = "task-tag-sync-1"

        res = OpenMetadataEventAdapterService(session, _settings()).process_change_event(event)

        assert res["status"] == "accepted"
        assert res["purposes"] == [EventPurpose.TAG_SYNC.value]
        mock_tag_sync.delay.assert_called_once_with(
            entity_type="table",
            entity_fqn="hive.sales.customers",
            correlation_id="om-event-evt-confirm-email",
        )


def test_non_tag_metadata_update_does_not_dispatch_backend_classification(session) -> None:
    event = {
        "id": "evt-description",
        "eventType": "ENTITY_UPDATED",
        "entityType": "table",
        "entityFullyQualifiedName": "hive.sales.customers",
        "timestamp": 456,
        "changeDescription": {
            "fieldsUpdated": [
                {"name": "description", "oldValue": "old", "newValue": "new"}
            ]
        },
    }

    with patch("app.services.openmetadata_event_adapter.sync_tags_to_ranger") as mock_tag_sync:
        res = OpenMetadataEventAdapterService(session, _settings()).process_change_event(event)

        assert res["status"] == "accepted"
        assert res["purposes"] == []
        assert res["dispatched_tasks"] == []
        mock_tag_sync.delay.assert_not_called()
