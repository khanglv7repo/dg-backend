"""Unit tests for EventPurposeRouter using real OpenMetadata event structures per OpenAPI spec."""
from __future__ import annotations

from app.services.event_router import EventPurpose, EventPurposeRouter


def test_real_om_entity_created_routes_classify_and_tag_sync() -> None:
    """Real OpenMetadata entityCreated event (camelCase string from OM OpenAPI schema)."""
    event = {
        "id": "c62f2776-9289-4e78-bc46-ee2e604f3238",
        "eventType": "entityCreated",
        "entityType": "table",
        "entityFullyQualifiedName": "postgresql_financial.financial_db.public.customer",
        "timestamp": 1722240000000,
        "entity": {
            "id": "99ef7491-0361-4202-b258-9418e2c7a52f",
            "name": "customer",
            "fullyQualifiedName": "postgresql_financial.financial_db.public.customer",
            "columns": [
                {"name": "customer_id", "dataType": "BIGINT"},
                {"name": "email_address", "dataType": "VARCHAR"},
            ],
        },
    }
    purposes = EventPurposeRouter.route(event)
    assert purposes == {EventPurpose.CLASSIFY, EventPurpose.TAG_SYNC}


def test_real_om_tag_only_change_routes_only_tag_sync() -> None:
    """Real OpenMetadata tag-only entityFieldsChanged event. MUST NOT route to CLASSIFY."""
    event = {
        "id": "e81d77a8-1234-5678-90ab-cdef12345678",
        "eventType": "entityFieldsChanged",
        "entityType": "table",
        "entityFullyQualifiedName": "postgresql_financial.financial_db.public.customer",
        "timestamp": 1722240005000,
        "changeDescription": {
            "fieldsAdded": [
                {
                    "name": "columns.email_address.tags",
                    "newValue": '[{"tagFQN": "PII.Email", "labelType": "Automated", "state": "Confirmed"}]',
                }
            ],
            "fieldsUpdated": [],
            "fieldsDeleted": [],
        },
    }
    purposes = EventPurposeRouter.route(event)
    assert purposes == {EventPurpose.TAG_SYNC}
    assert EventPurpose.CLASSIFY not in purposes, "Tag-only event MUST NOT route to CLASSIFY (prevents event loop!)"


def test_real_om_description_change_routes_classify_only() -> None:
    """Real OpenMetadata description change routes strictly to CLASSIFY."""
    event = {
        "id": "d1234567-89ab-cdef-0123-456789abcdef",
        "eventType": "entityFieldsChanged",
        "entityType": "table",
        "entityFullyQualifiedName": "postgresql_financial.financial_db.public.customer",
        "timestamp": 1722240010000,
        "changeDescription": {
            "fieldsAdded": [],
            "fieldsUpdated": [
                {
                    "name": "description",
                    "oldValue": "Customer table",
                    "newValue": "Stores customer contact email addresses and phone numbers",
                }
            ],
            "fieldsDeleted": [],
        },
    }
    purposes = EventPurposeRouter.route(event)
    assert purposes == {EventPurpose.CLASSIFY}


def test_real_om_structural_column_change_routes_both() -> None:
    """Real OpenMetadata column addition/deletion/update routes to CLASSIFY and TAG_SYNC."""
    event = {
        "id": "f9876543-21ba-fedc-3210-fedcba987654",
        "eventType": "entityFieldsChanged",
        "entityType": "table",
        "entityFullyQualifiedName": "postgresql_financial.financial_db.public.customer",
        "timestamp": 1722240015000,
        "changeDescription": {
            "fieldsAdded": [
                {
                    "name": "columns",
                    "newValue": '[{"name": "phone_num", "dataType": "VARCHAR"}]',
                }
            ],
            "fieldsUpdated": [],
            "fieldsDeleted": [],
        },
    }
    purposes = EventPurposeRouter.route(event)
    assert purposes == {EventPurpose.CLASSIFY, EventPurpose.TAG_SYNC}


def test_real_om_mixed_structural_and_tag_routes_both() -> None:
    """Mixed column structure + tag change routes to both CLASSIFY and TAG_SYNC."""
    event = {
        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "eventType": "entityFieldsChanged",
        "entityType": "table",
        "entityFullyQualifiedName": "postgresql_financial.financial_db.public.customer",
        "timestamp": 1722240020000,
        "changeDescription": {
            "fieldsAdded": [
                {"name": "columns", "newValue": '[{"name": "ssn"}]'},
                {"name": "tags", "newValue": '[{"tagFQN": "PII.Sensitive"}]'},
            ],
            "fieldsUpdated": [],
            "fieldsDeleted": [],
        },
    }
    purposes = EventPurposeRouter.route(event)
    assert purposes == {EventPurpose.CLASSIFY, EventPurpose.TAG_SYNC}


def test_real_om_owner_follower_change_routes_none() -> None:
    """Unrelated metadata changes (owner, followers, extension) produce empty set of purposes."""
    event = {
        "id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
        "eventType": "entityFieldsChanged",
        "entityType": "table",
        "entityFullyQualifiedName": "postgresql_financial.financial_db.public.customer",
        "timestamp": 1722240025000,
        "changeDescription": {
            "fieldsUpdated": [
                {
                    "name": "followers",
                    "oldValue": "[]",
                    "newValue": '[{"id": "user-uuid-1"}]',
                }
            ]
        },
    }
    purposes = EventPurposeRouter.route(event)
    assert purposes == set()
