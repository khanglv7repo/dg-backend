from unittest.mock import patch
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.core.errors import AuthorizationError
from app.repositories.watermark import IntegrationWatermarkRepository
from app.services.asset_discovery import AssetDiscoveryService
from app.services.event_router import EventPurpose
from app.services.openmetadata_event_adapter import OpenMetadataEventAdapterService


def test_webhook_adapter_auth_verification(session) -> None:
    settings = Settings(openmetadata_webhook_secret=SecretStr("super-secret"))
    adapter = OpenMetadataEventAdapterService(session, settings)

    adapter.verify_webhook_token("super-secret")

    with pytest.raises(AuthorizationError):
        adapter.verify_webhook_token("wrong-secret")


def test_webhook_adapter_entity_created_event(session) -> None:
    settings = Settings()
    adapter = OpenMetadataEventAdapterService(session, settings)

    raw_event = {
        "id": "evt-001",
        "eventType": "ENTITY_CREATED",
        "entityType": "table",
        "entityFullyQualifiedName": "hive.sales.customers",
        "entity": {
            "name": "customers",
            "columns": [
                {"name": "email_address", "dataType": "VARCHAR"},
                {"name": "phone_num", "dataType": "VARCHAR"},
            ],
        },
    }

    with patch("app.services.openmetadata_event_adapter.classify_entity") as mock_classify, \
         patch("app.services.openmetadata_event_adapter.sync_tags_to_ranger") as mock_tag_sync:
        mock_classify.delay.return_value.id = "task-c1"
        mock_tag_sync.delay.return_value.id = "task-t1"

        res = adapter.process_change_event(raw_event)

        assert res["status"] == "accepted"
        assert EventPurpose.CLASSIFY.value in res["purposes"]
        assert EventPurpose.TAG_SYNC.value in res["purposes"]
        mock_classify.delay.assert_called_once()
        mock_tag_sync.delay.assert_called_once()


def test_webhook_adapter_confirmed_tag_change(session) -> None:
    settings = Settings()
    adapter = OpenMetadataEventAdapterService(session, settings)

    raw_event = {
        "id": "evt-002",
        "eventType": "ENTITY_FIELDS_CHANGED",
        "entityType": "table",
        "entityFullyQualifiedName": "hive.sales.customers",
        "changeDescription": {
            "fieldsAdded": [{"name": "tags", "newValue": "PII.Email"}]
        },
        "entity": {
            "name": "customers",
            "tags": [{"tagFQN": "Sensitivity.Confidential", "state": "Confirmed"}],
            "columns": [
                {
                    "name": "email",
                    "tags": [{"tagFQN": "PII.Email", "state": "Confirmed"}],
                }
            ],
        },
    }

    with patch("app.services.openmetadata_event_adapter.classify_entity") as mock_classify, \
         patch("app.services.openmetadata_event_adapter.sync_tags_to_ranger") as mock_tag_sync:
        mock_tag_sync.delay.return_value.id = "task-t2"

        res = adapter.process_change_event(raw_event)

        assert res["status"] == "accepted"
        assert res["purposes"] == [EventPurpose.TAG_SYNC.value]
        mock_classify.delay.assert_not_called()
        mock_tag_sync.delay.assert_called_once_with(
            entity_type="table",
            entity_fqn="hive.sales.customers",
            correlation_id="om-event-evt-002",
        )


def test_watermark_repository(session) -> None:
    repo = IntegrationWatermarkRepository(session)
    val = repo.get("openmetadata", "asset_discovery_last_timestamp")
    assert val is None

    repo.set("openmetadata", "asset_discovery_last_timestamp", "1700000000000")
    assert repo.get("openmetadata", "asset_discovery_last_timestamp") == "1700000000000"


def test_asset_discovery_service_skips_when_disabled(session) -> None:
    settings = Settings(openmetadata_enabled=False)
    service = AssetDiscoveryService(session, settings)
    res = service.discover()
    assert res["status"] == "SKIPPED"
