"""Contract tests verifying OpenMetadata ChangeEvent schema compatibility with metadata.generated SDK models."""
from __future__ import annotations

from metadata.generated.schema.type.changeEvent import ChangeEvent

from app.schemas.openmetadata_events import OpenMetadataChangeEventRequest
from app.services.event_router import EventPurpose, EventPurposeRouter


def test_openmetadata_change_event_official_sdk_contract() -> None:
    """Validate raw payload against official OpenMetadata metadata.generated.schema.type.changeEvent.ChangeEvent model."""
    raw_om_payload = {
        "id": "7b8e9f1a-2c3d-4e5f-6a7b-8c9d0e1f2a3b",
        "eventType": "entityFieldsChanged",
        "entityType": "table",
        "entityId": "11223344-5566-7788-9900-aabbccddeeff",
        "entityFullyQualifiedName": "trino_prod.finance.public.payments",
        "timestamp": 1722240100000,
        "changeDescription": {
            "fieldsAdded": [
                {
                    "name": "columns.card_number.tags",
                    "newValue": '[{"tagFQN": "PII.CreditCard", "state": "Confirmed"}]',
                }
            ],
            "fieldsUpdated": [],
            "fieldsDeleted": [],
            "previousVersion": 0.1,
        },
        "incrementalChangeDescription": {
            "fieldsAdded": [],
            "fieldsUpdated": [],
            "fieldsDeleted": [],
        },
        "entity": {
            "id": "11223344-5566-7788-9900-aabbccddeeff",
            "name": "payments",
            "fullyQualifiedName": "trino_prod.finance.public.payments",
        },
    }

    # 1. Official OpenMetadata SDK ChangeEvent model validation
    sdk_event = ChangeEvent.model_validate(raw_om_payload)
    assert "7b8e9f1a-2c3d-4e5f-6a7b-8c9d0e1f2a3b" in str(sdk_event.id)
    assert getattr(sdk_event.eventType, "value", sdk_event.eventType) == "entityFieldsChanged"
    assert sdk_event.entityType == "table"
    assert sdk_event.entityFullyQualifiedName == "trino_prod.finance.public.payments"

    # 2. Backend OpenMetadataChangeEventRequest validation
    backend_req = OpenMetadataChangeEventRequest.model_validate(raw_om_payload)
    assert backend_req.id == "7b8e9f1a-2c3d-4e5f-6a7b-8c9d0e1f2a3b"

    # 3. Router logic
    purposes = EventPurposeRouter.route(raw_om_payload)
    assert purposes == {EventPurpose.TAG_SYNC}
