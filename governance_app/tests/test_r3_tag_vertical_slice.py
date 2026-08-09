"""Unit tests for R3 TAG Vertical Slice requirements."""
from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from app.clients.openmetadata import OpenMetadataClient
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


def test_deterministic_match_writes_confirmed_tag_directly_without_suggestion() -> None:
    mock_om_client = MagicMock(spec=OpenMetadataClient, unsafe=True)
    mock_om_client.get_entity.return_value = {
        "name": "customer",
        "description": "customer data",
        "columns": [{"name": "phone_number", "dataType": "VARCHAR", "description": "customer phone number"}],
    }

    mock_eval_result = MagicMock()
    mock_eval_result.outcome = MatchOutcome.EXACT
    mock_sugg = MagicMock()
    mock_sugg.field_path = "columns.phone_number"
    mock_sugg.tag = "PII.Phone"
    mock_sugg.model_dump.return_value = {"field_path": "columns.phone_number", "tag": "PII.Phone"}
    mock_eval_result.suggestions = [mock_sugg]
    mock_eval_result.evidence = {"rule": "phone_rule"}

    mock_engine = MagicMock()
    mock_engine.evaluate.return_value = mock_eval_result

    with patch("app.tasks.classification.OpenMetadataClient", return_value=mock_om_client), \
         patch("app.tasks.classification.ClassificationRuleCatalogService") as mock_catalog, \
         patch("app.tasks.classification.SessionLocal") as mock_session_local:

        mock_session = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_session
        mock_catalog.return_value.active_engine.return_value = mock_engine

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


def test_ai_fallback_outcomes_transition_to_waiting_ai() -> None:
    for outcome in (MatchOutcome.NO_MATCH, MatchOutcome.AMBIGUOUS):
        mock_om_client = MagicMock(spec=OpenMetadataClient, unsafe=True)
        mock_om_client.get_entity.return_value = {
            "name": "customer",
            "columns": [{"name": "id", "dataType": "INT"}],
        }

        mock_eval_result = MagicMock()
        mock_eval_result.outcome = outcome
        mock_eval_result.suggestions = []
        mock_eval_result.evidence = {}

        mock_engine = MagicMock()
        mock_engine.evaluate.return_value = mock_eval_result

        with patch("app.tasks.classification.OpenMetadataClient", return_value=mock_om_client), \
             patch("app.tasks.classification.ClassificationRuleCatalogService") as mock_catalog, \
             patch("app.tasks.classification.ai_classify_entity") as mock_ai_task, \
             patch("app.tasks.classification.SessionLocal") as mock_session_local:

            mock_session = MagicMock()
            mock_session_local.return_value.__enter__.return_value = mock_session
            mock_catalog.return_value.active_engine.return_value = mock_engine

            result = classify_entity(
                event_id=f"evt-{outcome.value}-1",
                entity_type="table",
                entity_fqn="financial_db.public.customer",
            )

            assert result["status"] == "WAITING_AI"
            assert result["generation"] == 1
            mock_ai_task.delay.assert_called_once_with(
                execution_id=result["execution_id"],
                generation=1,
            )
