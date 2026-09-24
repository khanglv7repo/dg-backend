from unittest.mock import patch

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.core.errors import AuthorizationError
from app.repositories.watermark import IntegrationWatermarkRepository
from app.services.event_router import EventPurpose
from app.services.openmetadata_event_adapter import OpenMetadataEventAdapterService


def test_webhook_adapter_auth_verification(session) -> None:
    settings = Settings(openmetadata_webhook_secret=SecretStr("super-secret"))
    adapter = OpenMetadataEventAdapterService(session, settings)

    adapter.verify_webhook_token("super-secret")

    with pytest.raises(AuthorizationError):
        adapter.verify_webhook_token("wrong-secret")


def test_webhook_adapter_entity_created_only_syncs_existing_om_tags(session) -> None:
    adapter = OpenMetadataEventAdapterService(session, Settings())
    raw_event = {
        "id": "evt-001",
        "eventType": "ENTITY_CREATED",
        "entityType": "table",
        "entityFullyQualifiedName": "hive.sales.customers",
    }

    with patch("app.services.openmetadata_event_adapter.sync_tags_to_ranger") as sync:
        sync.delay.return_value.id = "task-t1"
        result = adapter.process_change_event(raw_event)

    assert result["status"] == "accepted"
    assert result["purposes"] == [EventPurpose.TAG_SYNC.value]
    sync.delay.assert_called_once()


def test_webhook_adapter_confirmed_tag_change_syncs_ranger(session) -> None:
    adapter = OpenMetadataEventAdapterService(session, Settings())
    raw_event = {
        "id": "evt-002",
        "eventType": "ENTITY_FIELDS_CHANGED",
        "entityType": "table",
        "entityFullyQualifiedName": "hive.sales.customers",
        "changeDescription": {
            "fieldsAdded": [{"name": "tags", "newValue": "PII.Email"}]
        },
    }

    with patch("app.services.openmetadata_event_adapter.sync_tags_to_ranger") as sync:
        sync.delay.return_value.id = "task-t2"
        result = adapter.process_change_event(raw_event)

    assert result["purposes"] == [EventPurpose.TAG_SYNC.value]
    sync.delay.assert_called_once_with(
        entity_type="table",
        entity_fqn="hive.sales.customers",
        correlation_id="om-event-evt-002",
    )


def test_watermark_repository_remains_available_for_integration_state(session) -> None:
    repo = IntegrationWatermarkRepository(session)
    assert repo.get("openmetadata", "last_timestamp") is None

    repo.set("openmetadata", "last_timestamp", "1700000000000")
    assert repo.get("openmetadata", "last_timestamp") == "1700000000000"
