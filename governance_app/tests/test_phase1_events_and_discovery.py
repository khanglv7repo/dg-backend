import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.core.errors import AuthorizationError
from app.models.enums import JobType
from app.repositories.jobs import JobRepository
from app.repositories.watermark import IntegrationWatermarkRepository
from app.services.asset_discovery import AssetDiscoveryService
from app.services.openmetadata_event_adapter import OpenMetadataEventAdapterService


def test_webhook_adapter_auth_verification(session) -> None:
    settings = Settings(openmetadata_webhook_secret=SecretStr("super-secret"))
    adapter = OpenMetadataEventAdapterService(session, settings)

    # Valid secret
    adapter.verify_webhook_token("super-secret")

    # Invalid secret
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

    with session.begin():
        job_ids = adapter.process_change_event(raw_event)

    assert len(job_ids) == 1
    job = JobRepository(session).get(job_ids[0])
    assert job.job_type == JobType.CLASSIFY_ASSET.value
    assert job.payload["entity_fqn"] == "hive.sales.customers"


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

    with session.begin():
        job_ids = adapter.process_change_event(raw_event)

    # Should enqueue both RECONCILE_RANGER (for tag change) and CLASSIFY_ASSET (for fields changed)
    assert len(job_ids) == 2
    types = {JobRepository(session).get(jid).job_type for jid in job_ids}
    assert JobType.RECONCILE_RANGER.value in types
    assert JobType.CLASSIFY_ASSET.value in types


def test_watermark_repository(session) -> None:
    repo = IntegrationWatermarkRepository(session)
    with session.begin():
        val = repo.get("openmetadata", "asset_discovery_last_timestamp")
        assert val is None

        repo.set("openmetadata", "asset_discovery_last_timestamp", "1700000000000")

    assert repo.get("openmetadata", "asset_discovery_last_timestamp") == "1700000000000"


def test_asset_discovery_service_skips_when_disabled(session) -> None:
    settings = Settings(openmetadata_enabled=False)
    service = AssetDiscoveryService(session, settings)
    with session.begin():
        res = service.discover()
    assert res["status"] == "SKIPPED"
