"""Unit tests for R3 TAG Vertical Slice requirements."""
from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from app.clients.openmetadata import OpenMetadataClient
from app.repositories.classification_rule_versions import ClassificationRuleVersionRepository
from app.repositories.event_inbox import EventInboxRepository
from app.schemas.classification import MatchOutcome
from app.services.event_router import EventPurpose, EventPurposeRouter
from app.tasks.classification import classify_entity
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
    purposes = EventPurposeRouter.route(event)
    assert purposes == {EventPurpose.TAG_SYNC}
    assert EventPurpose.CLASSIFY not in purposes, "Tag-only event MUST NOT route to CLASSIFY (loop prevention!)"


def test_event_router_classification_input_routes_to_classify() -> None:
    event = {
        "eventType": "entityFieldsChanged",
        "entityType": "table",
        "entityFullyQualifiedName": "financial_db.public.customer",
        "changeDescription": {
            "fieldsUpdated": [{"name": "description", "oldValue": "old", "newValue": "Customer phone numbers"}]
        },
    }
    purposes = EventPurposeRouter.route(event)
    assert purposes == {EventPurpose.CLASSIFY}


def test_event_router_structural_column_event_routes_to_both() -> None:
    event = {
        "eventType": "entityFieldsChanged",
        "entityType": "table",
        "entityFullyQualifiedName": "financial_db.public.customer",
        "changeDescription": {
            "fieldsAdded": [{"name": "columns", "newValue": '[{"name": "phone"}]'}]
        },
    }
    purposes = EventPurposeRouter.route(event)
    assert purposes == {EventPurpose.CLASSIFY, EventPurpose.TAG_SYNC}


def test_event_router_unrelated_event_routes_to_none() -> None:
    event = {
        "eventType": "entityFieldsChanged",
        "entityType": "table",
        "entityFullyQualifiedName": "financial_db.public.customer",
        "changeDescription": {
            "fieldsUpdated": [{"name": "followers", "newValue": "user1"}]
        },
    }
    purposes = EventPurposeRouter.route(event)
    assert purposes == set()


def test_event_router_mixed_tag_and_structural_routes_to_both() -> None:
    event = {
        "eventType": "entityFieldsChanged",
        "entityType": "table",
        "entityFullyQualifiedName": "financial_db.public.customer",
        "changeDescription": {
            "fieldsAdded": [
                {"name": "tags", "newValue": "PII.Phone"},
                {"name": "columns", "newValue": "phone_col"},
            ]
        },
    }
    purposes = EventPurposeRouter.route(event)
    assert purposes == {EventPurpose.CLASSIFY, EventPurpose.TAG_SYNC}


def test_event_inbox_deduplicates_duplicate_event_delivery(session) -> None:
    repo = EventInboxRepository(session)
    with session.begin():
        record1, dup1 = repo.record_event(
            event_id="evt-12345",
            event_type="entityFieldsChanged",
            entity_type="table",
            entity_fqn="financial_db.public.customer",
            payload={"foo": "bar"},
            purposes=["CLASSIFY"],
        )
    assert dup1 is False
    assert record1.event_id == "evt-12345"

    with session.begin():
        record2, dup2 = repo.record_event(
            event_id="evt-12345",
            event_type="entityFieldsChanged",
            entity_type="table",
            entity_fqn="financial_db.public.customer",
            payload={"foo": "bar"},
            purposes=["CLASSIFY"],
        )
    assert dup2 is True
    assert record2.id == record1.id


def test_sync_tags_to_ranger_uses_correct_queue() -> None:
    assert sync_tags_to_ranger.queue == "ranger.tag-sync"


def _activate_rule_version(session, document: dict) -> None:
    repo = ClassificationRuleVersionRepository(session)
    record = repo.create(
        payload=document,
        checksum=f"checksum-{document['version']}",
        declared_version=document["version"],
        created_by="test-suite",
    )
    repo.activate(record.id)
    session.commit()


class _SessionContext:
    def __init__(self, session):
        self.session = session

    def __enter__(self):
        return self.session

    def __exit__(self, exc_type, exc, tb):
        return False


def test_deterministic_match_writes_confirmed_tag_directly_without_suggestion(session) -> None:
    _activate_rule_version(
        session,
        {
            "version": "task-direct-match",
            "rules": [
                {
                    "id": "phone-exact",
                    "target": "column",
                    "when": {"name_exact": ["phone_number"]},
                    "tag": "PII.Phone",
                    "confidence": 1.0,
                    "auto_apply": True,
                }
            ],
        },
    )

    mock_om_client = MagicMock(spec=OpenMetadataClient, unsafe=True)
    mock_om_client.get_entity.return_value = {
        "name": "customer",
        "description": "customer data",
        "columns": [{"name": "phone_number", "dataType": "VARCHAR", "description": "customer phone number"}],
    }

    with patch("app.tasks.classification.OpenMetadataClient", return_value=mock_om_client), \
         patch("app.tasks.classification.SessionLocal") as mock_session_local:

        mock_session_local.return_value = _SessionContext(session)

        result = classify_entity(
            event_id="evt-match-1",
            entity_type="table",
            entity_fqn="financial_db.public.customer",
        )

        assert result["status"] == "COMPLETED"
        assert result["outcome"] == "MATCH"

        # Verify direct tag write occurred
        mock_om_client.apply_confirmed_tags.assert_called_once()
        mock_om_client.assert_confirmed_tags.assert_called_once()
        # Verify Suggestion was NOT created
        mock_om_client.create_tag_suggestion.assert_not_called()


def test_ai_fallback_outcomes_transition_to_waiting_ai(session) -> None:
    for outcome in (MatchOutcome.NO_MATCH, MatchOutcome.AMBIGUOUS):
        document = {
            "version": f"task-{outcome.value}",
            "rules": [
                {
                    "id": "email-exact",
                    "target": "column",
                    "when": {"name_exact": ["email"]},
                    "tag": "PII.Email",
                    "confidence": 1.0,
                    "auto_apply": True,
                }
            ],
        }
        if outcome == MatchOutcome.AMBIGUOUS:
            document["rules"] = [
                {
                    "id": "id-sensitive",
                    "target": "column",
                    "when": {"name_exact": ["id"]},
                    "tag": "PII.CustomerID",
                    "confidence": 0.9,
                    "auto_apply": True,
                },
                {
                    "id": "id-financial",
                    "target": "column",
                    "when": {"name_exact": ["id"]},
                    "tag": "Financial.AccountID",
                    "confidence": 0.9,
                    "auto_apply": True,
                },
            ]
        _activate_rule_version(session, document)

        mock_om_client = MagicMock(spec=OpenMetadataClient, unsafe=True)
        mock_om_client.get_entity.return_value = {
            "name": "customer",
            "columns": [{"name": "id", "dataType": "INT"}],
        }

        with patch("app.tasks.classification.OpenMetadataClient", return_value=mock_om_client), \
             patch("app.tasks.classification.ai_classify_entity") as mock_ai_task, \
             patch("app.tasks.classification.SessionLocal") as mock_session_local:

            mock_session_local.return_value = _SessionContext(session)

            result = classify_entity(
                event_id=f"evt-{outcome.value}-1",
                entity_type="table",
                entity_fqn="financial_db.public.customer",
            )

            assert result["status"] == "WAITING_AI"
            assert result["generation"] >= 1
            mock_ai_task.delay.assert_called_once_with(
                execution_id=result["execution_id"],
                generation=result["generation"],
            )
