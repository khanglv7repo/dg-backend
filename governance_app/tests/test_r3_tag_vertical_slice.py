"""Runtime tests for the retained TAG_SYNC vertical slice."""
from __future__ import annotations

from app.repositories.event_inbox import EventInboxRepository
from app.services.event_router import EventPurpose, EventPurposeRouter
from app.tasks.tag_sync import sync_tags_to_ranger


def test_event_router_tag_only_change_routes_to_tag_sync_only() -> None:
    event = {
        "eventType": "entityFieldsChanged",
        "entityType": "table",
        "entityFullyQualifiedName": "financial_db.public.customer",
        "changeDescription": {
            "fieldsAdded": [{"name": "tags", "newValue": '[{"tagFQN": "PII.Phone"}]'}]
        },
    }
    assert EventPurposeRouter.route(event) == {EventPurpose.TAG_SYNC}


def test_event_router_description_change_routes_to_none() -> None:
    event = {
        "eventType": "entityFieldsChanged",
        "entityType": "table",
        "entityFullyQualifiedName": "financial_db.public.customer",
        "changeDescription": {
            "fieldsUpdated": [{"name": "description", "oldValue": "old", "newValue": "new"}]
        },
    }
    assert EventPurposeRouter.route(event) == set()


def test_event_router_entity_created_routes_to_tag_sync_only() -> None:
    event = {
        "eventType": "entityCreated",
        "entityType": "table",
        "entityFullyQualifiedName": "financial_db.public.customer",
    }
    assert EventPurposeRouter.route(event) == {EventPurpose.TAG_SYNC}


def test_event_inbox_deduplicates_duplicate_event_delivery(session) -> None:
    repo = EventInboxRepository(session)
    with session.begin():
        record1, dup1 = repo.record_event(
            event_id="evt-12345",
            event_type="entityFieldsChanged",
            entity_type="table",
            entity_fqn="financial_db.public.customer",
            payload={"foo": "bar"},
            purposes=[EventPurpose.TAG_SYNC.value],
        )
    assert dup1 is False

    with session.begin():
        record2, dup2 = repo.record_event(
            event_id="evt-12345",
            event_type="entityFieldsChanged",
            entity_type="table",
            entity_fqn="financial_db.public.customer",
            payload={"foo": "bar"},
            purposes=[EventPurpose.TAG_SYNC.value],
        )
    assert dup2 is True
    assert record2.id == record1.id


def test_sync_tags_to_ranger_uses_correct_queue() -> None:
    assert sync_tags_to_ranger.queue == "ranger.tag-sync"
