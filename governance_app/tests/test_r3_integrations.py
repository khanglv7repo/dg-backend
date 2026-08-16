"""R3 Integration Test Suite crossing real production boundaries."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, create_autospec, patch
import pytest

from app.clients.openmetadata import OpenMetadataClient
from app.clients.ranger_tags import RangerTagStoreClient
from app.core.config import Settings
from app.models.classification_execution import ClassificationExecution
from app.models.event_inbox import EventInbox
from app.schemas.classification import MatchOutcome
from app.services.event_router import EventPurpose
from app.services.openmetadata_event_adapter import OpenMetadataEventAdapterService
from app.tasks.classification import classify_entity
from app.tasks.tag_sync import sync_tags_to_ranger


def _settings() -> Settings:
    return Settings(_env_file=None)


# -----------------------------------------------------------------------------
# Integration A — Webhook -> Inbox -> EventPurposeRouter -> Celery dispatch
# -----------------------------------------------------------------------------
def test_integration_a_webhook_to_inbox_to_dispatch(session) -> None:
    raw_om_event = {
        "id": "evt-integ-a-001",
        "eventType": "entityCreated",
        "entityType": "table",
        "entityFullyQualifiedName": "trino_prod.analytics.customer_orders",
        "timestamp": 1722240500000,
        "entity": {
            "name": "customer_orders",
            "columns": [{"name": "order_id", "dataType": "BIGINT"}],
        },
    }

    with patch("app.services.openmetadata_event_adapter.classify_entity") as mock_classify, \
         patch("app.services.openmetadata_event_adapter.sync_tags_to_ranger") as mock_tag_sync:

        mock_classify.delay.return_value.id = "task-c-a1"
        mock_tag_sync.delay.return_value.id = "task-t-a1"

        adapter = OpenMetadataEventAdapterService(session, _settings())
        res = adapter.process_change_event(raw_om_event)

        assert res["status"] == "accepted"
        assert res["event_id"] == "evt-integ-a-001"
        assert EventPurpose.CLASSIFY.value in res["purposes"]
        assert EventPurpose.TAG_SYNC.value in res["purposes"]

        # Verify event_inbox record persisted in DB
        inbox_rec = session.query(EventInbox).filter(EventInbox.event_id == "evt-integ-a-001").first()
        assert inbox_rec is not None
        assert inbox_rec.status == "PROCESSED"

        mock_classify.delay.assert_called_once_with(
            event_id="evt-integ-a-001",
            entity_type="table",
            entity_fqn="trino_prod.analytics.customer_orders",
            correlation_id="om-event-evt-integ-a-001",
        )
        mock_tag_sync.delay.assert_called_once_with(
            entity_type="table",
            entity_fqn="trino_prod.analytics.customer_orders",
            correlation_id="om-event-evt-integ-a-001",
        )


# -----------------------------------------------------------------------------
# Integration B — Deterministic MATCH -> direct OM tag write -> OM read-back
# -----------------------------------------------------------------------------
def test_integration_b_deterministic_match_direct_om_write(session) -> None:
    from app.services.classification_rule_catalog import ClassificationRuleCatalogService
    rule_doc = {
        "version": "1.0",
        "rules": [
            {
                "id": "rule_phone",
                "target": "column",
                "when": {"name_exact": ["phone_number"]},
                "tag": "PII.Phone",
                "auto_apply": True,
            }
        ],
    }

    catalog = ClassificationRuleCatalogService(session)
    catalog.import_json(
        json.dumps(rule_doc).encode(),
        filename="rules.json",
        actor_id="admin",
        actor_name="Admin",
        activate=True,
    )

    mock_om_client = MagicMock(spec=OpenMetadataClient, unsafe=True)
    mock_om_client.get_entity.return_value = {
        "name": "customer",
        "description": "customer records",
        "columns": [{"name": "phone_number", "dataType": "VARCHAR", "description": "customer mobile phone"}],
    }
    mock_om_client.apply_confirmed_tags.return_value = {"entity": {}, "columns": {}}
    mock_om_client.assert_confirmed_tags.return_value = None

    with patch("app.tasks.classification.OpenMetadataClient", return_value=mock_om_client), \
         patch("app.tasks.classification.SessionLocal", return_value=session):

        result = classify_entity(
            event_id="evt-integ-b-001",
            entity_type="table",
            entity_fqn="trino_prod.analytics.customer",
        )

        assert result["status"] == "COMPLETED"
        assert result["outcome"] == "MATCH"

        mock_om_client.apply_confirmed_tags.assert_called_once()
        mock_om_client.assert_confirmed_tags.assert_called_once()
        mock_om_client.create_tag_suggestion.assert_not_called()

        execution = session.query(ClassificationExecution).one()
        assert execution.rule_version_id is not None


def test_execution_records_exact_rule_version(session) -> None:
    from app.services.classification_rule_catalog import ClassificationRuleCatalogService

    rule_doc = {
        "version": "bind-rule-version",
        "rules": [
            {
                "id": "rule_phone",
                "target": "column",
                "when": {"name_exact": ["phone"]},
                "tag": "PII.Phone",
                "auto_apply": True,
            }
        ],
    }
    active_rule, _created, _activated = ClassificationRuleCatalogService(session).import_json(
        json.dumps(rule_doc).encode(),
        filename="rules-bind.json",
        actor_id="admin",
        actor_name="Admin",
        activate=True,
    )

    mock_om_client = MagicMock(spec=OpenMetadataClient, unsafe=True)
    mock_om_client.get_entity.return_value = {
        "name": "customer",
        "columns": [{"name": "phone", "dataType": "VARCHAR"}],
    }
    mock_om_client.apply_confirmed_tags.return_value = {"entity": {}, "columns": {}}
    mock_om_client.assert_confirmed_tags.return_value = None

    with patch("app.tasks.classification.OpenMetadataClient", return_value=mock_om_client), \
         patch("app.tasks.classification.SessionLocal", return_value=session):
        result = classify_entity(
            event_id="evt-bind-rule",
            entity_type="table",
            entity_fqn="trino_prod.analytics.customer",
        )

    execution = session.query(ClassificationExecution).one()
    assert result["rule_version_id"] == str(active_rule.id)
    assert execution.rule_version_id == str(active_rule.id)


def test_generation_stale_guard_prevents_authoritative_write(session) -> None:
    from app.repositories.classification_execution import ClassificationExecutionRepository
    from app.services.classification_rule_catalog import ClassificationRuleCatalogService

    rule_doc = {
        "version": "stale-generation",
        "rules": [
            {
                "id": "rule_phone",
                "target": "column",
                "when": {"name_exact": ["phone"]},
                "tag": "PII.Phone",
                "auto_apply": True,
            }
        ],
    }
    ClassificationRuleCatalogService(session).import_json(
        json.dumps(rule_doc).encode(),
        filename="rules-stale-generation.json",
        actor_id="admin",
        actor_name="Admin",
        activate=True,
    )

    mock_om_client = MagicMock(spec=OpenMetadataClient, unsafe=True)
    mock_om_client.get_entity.return_value = {
        "name": "customer",
        "columns": [{"name": "phone", "dataType": "VARCHAR"}],
    }

    def supersede_generation(_tags: list[str]) -> None:
        ClassificationExecutionRepository(session).create_next_generation(
            event_id="evt-newer-generation",
            entity_type="table",
            entity_fqn="trino_prod.analytics.customer",
            status="EVALUATING",
        )

    mock_om_client.validate_tag_fqns.side_effect = supersede_generation

    with patch("app.tasks.classification.OpenMetadataClient", return_value=mock_om_client), \
         patch("app.tasks.classification.SessionLocal", return_value=session):
        result = classify_entity(
            event_id="evt-stale-generation",
            entity_type="table",
            entity_fqn="trino_prod.analytics.customer",
        )

    assert result["status"] == "SUPERSEDED"
    assert result["reason"] == "generation_not_current"
    mock_om_client.apply_confirmed_tags.assert_not_called()


def test_rule_version_change_prevents_stale_authoritative_write(session) -> None:
    from app.services.classification_rule_catalog import ClassificationRuleCatalogService

    catalog = ClassificationRuleCatalogService(session)
    rule_v1 = {
        "version": "rule-v1",
        "rules": [
            {
                "id": "rule_phone",
                "target": "column",
                "when": {"name_exact": ["phone"]},
                "tag": "PII.Phone",
                "auto_apply": True,
            }
        ],
    }
    rule_v2 = {
        "version": "rule-v2",
        "rules": [
            {
                "id": "rule_email",
                "target": "column",
                "when": {"name_exact": ["email"]},
                "tag": "PII.Email",
                "auto_apply": True,
            }
        ],
    }
    catalog.import_json(
        json.dumps(rule_v1).encode(),
        filename="rules-v1.json",
        actor_id="admin",
        actor_name="Admin",
        activate=True,
    )

    mock_om_client = MagicMock(spec=OpenMetadataClient, unsafe=True)
    mock_om_client.get_entity.return_value = {
        "name": "customer",
        "columns": [{"name": "phone", "dataType": "VARCHAR"}],
    }

    def activate_new_rule(_tags: list[str]) -> None:
        catalog.import_json(
            json.dumps(rule_v2).encode(),
            filename="rules-v2.json",
            actor_id="admin",
            actor_name="Admin",
            activate=True,
        )

    mock_om_client.validate_tag_fqns.side_effect = activate_new_rule

    with patch("app.tasks.classification.OpenMetadataClient", return_value=mock_om_client), \
         patch("app.tasks.classification.SessionLocal", return_value=session):
        result = classify_entity(
            event_id="evt-stale-rule",
            entity_type="table",
            entity_fqn="trino_prod.analytics.customer",
        )

    assert result["status"] == "SUPERSEDED"
    assert result["reason"] == "rule_version_not_active"
    mock_om_client.apply_confirmed_tags.assert_not_called()


def test_duplicate_classify_task_delivery_reuses_logical_execution(session) -> None:
    from app.services.classification_rule_catalog import ClassificationRuleCatalogService

    rule_doc = {
        "version": "duplicate-event",
        "rules": [
            {
                "id": "rule_phone",
                "target": "column",
                "when": {"name_exact": ["phone"]},
                "tag": "PII.Phone",
                "auto_apply": True,
            }
        ],
    }
    ClassificationRuleCatalogService(session).import_json(
        json.dumps(rule_doc).encode(),
        filename="rules-duplicate.json",
        actor_id="admin",
        actor_name="Admin",
        activate=True,
    )
    mock_om_client = MagicMock(spec=OpenMetadataClient, unsafe=True)
    mock_om_client.get_entity.return_value = {
        "name": "customer",
        "columns": [{"name": "phone", "dataType": "VARCHAR"}],
    }
    mock_om_client.apply_confirmed_tags.return_value = {"entity": {}, "columns": {}}
    mock_om_client.assert_confirmed_tags.return_value = None

    with patch("app.tasks.classification.OpenMetadataClient", return_value=mock_om_client), \
         patch("app.tasks.classification.SessionLocal", return_value=session):
        first = classify_entity(
            event_id="evt-duplicate",
            entity_type="table",
            entity_fqn="trino_prod.analytics.customer",
        )
        second = classify_entity(
            event_id="evt-duplicate",
            entity_type="table",
            entity_fqn="trino_prod.analytics.customer",
        )

    assert first["execution_id"] == second["execution_id"]
    assert second["duplicate"] is True
    assert session.query(ClassificationExecution).count() == 1
    assert mock_om_client.apply_confirmed_tags.call_count == 1


def test_different_event_for_same_entity_creates_next_generation(session) -> None:
    from app.services.classification_rule_catalog import ClassificationRuleCatalogService

    rule_doc = {
        "version": "different-event",
        "rules": [
            {
                "id": "rule_phone",
                "target": "column",
                "when": {"name_exact": ["phone"]},
                "tag": "PII.Phone",
                "auto_apply": True,
            }
        ],
    }
    ClassificationRuleCatalogService(session).import_json(
        json.dumps(rule_doc).encode(),
        filename="rules-different-event.json",
        actor_id="admin",
        actor_name="Admin",
        activate=True,
    )
    mock_om_client = MagicMock(spec=OpenMetadataClient, unsafe=True)
    mock_om_client.get_entity.return_value = {
        "name": "customer",
        "columns": [{"name": "phone", "dataType": "VARCHAR"}],
    }
    mock_om_client.apply_confirmed_tags.return_value = {"entity": {}, "columns": {}}
    mock_om_client.assert_confirmed_tags.return_value = None

    with patch("app.tasks.classification.OpenMetadataClient", return_value=mock_om_client), \
         patch("app.tasks.classification.SessionLocal", return_value=session):
        first = classify_entity(
            event_id="evt-generation-1",
            entity_type="table",
            entity_fqn="trino_prod.analytics.customer",
        )
        second = classify_entity(
            event_id="evt-generation-2",
            entity_type="table",
            entity_fqn="trino_prod.analytics.customer",
        )

    assert second["generation"] == first["generation"] + 1
    assert session.query(ClassificationExecution).count() == 2


# -----------------------------------------------------------------------------
# Integration C — AI Fallback (NO_MATCH / AMBIGUOUS / CONFLICT)
# -----------------------------------------------------------------------------
def test_integration_c_ai_fallback_handoff(session) -> None:
    from app.services.classification_rule_catalog import ClassificationRuleCatalogService
    rule_doc = {
        "version": "1.0",
        "rules": [
            {
                "id": "r1",
                "target": "column",
                "when": {"name_exact": ["unused_column_name"]},
                "tag": "PII.Email",
                "auto_apply": True,
            }
        ],
    }
    catalog = ClassificationRuleCatalogService(session)
    catalog.import_json(
        json.dumps(rule_doc).encode(),
        filename="rules_c.json",
        actor_id="admin",
        actor_name="Admin",
        activate=True,
    )

    mock_om_client = MagicMock(spec=OpenMetadataClient, unsafe=True)
    mock_om_client.get_entity.return_value = {
        "name": "raw_logs",
        "columns": [{"name": "log_id", "dataType": "BIGINT"}],
    }

    with patch("app.tasks.classification.OpenMetadataClient", return_value=mock_om_client), \
         patch("app.tasks.classification.ai_classify_entity") as mock_ai_task, \
         patch("app.tasks.classification.SessionLocal", return_value=session):

        result = classify_entity(
            event_id="evt-integ-c-nomatch",
            entity_type="table",
            entity_fqn="trino_prod.raw.logs",
        )

        assert result["status"] == "WAITING_AI"
        assert result["generation"] >= 1

        mock_ai_task.delay.assert_called_once_with(
            execution_id=result["execution_id"],
            generation=result["generation"],
        )


def test_waiting_ai_duplicate_republishes_existing_ai_handoff(session) -> None:
    from app.services.classification_rule_catalog import ClassificationRuleCatalogService

    rule_doc = {
        "version": "waiting-ai-republish",
        "rules": [
            {
                "id": "rule_unused",
                "target": "column",
                "when": {"name_exact": ["unused_column_name"]},
                "tag": "PII.Email",
                "auto_apply": True,
            }
        ],
    }
    ClassificationRuleCatalogService(session).import_json(
        json.dumps(rule_doc).encode(),
        filename="rules-waiting-ai-republish.json",
        actor_id="admin",
        actor_name="Admin",
        activate=True,
    )

    mock_om_client = MagicMock(spec=OpenMetadataClient, unsafe=True)
    mock_om_client.get_entity.return_value = {
        "name": "raw_logs",
        "columns": [{"name": "log_id", "dataType": "BIGINT"}],
    }
    successful_publish = MagicMock()
    successful_publish.id = "ai-task-republished"

    with patch("app.tasks.classification.OpenMetadataClient", return_value=mock_om_client), \
         patch("app.tasks.classification.ai_classify_entity") as mock_ai_task, \
         patch("app.tasks.classification.SessionLocal", return_value=session):
        mock_ai_task.delay.side_effect = [
            RuntimeError("broker publish failed"),
            successful_publish,
        ]

        with pytest.raises(Exception):
            classify_entity(
                event_id="evt-waiting-ai-republish",
                entity_type="table",
                entity_fqn="trino_prod.raw.logs",
            )

        existing = session.query(ClassificationExecution).one()
        assert existing.status == "WAITING_AI"
        assert existing.outcome == "NO_MATCH"

        duplicate = classify_entity(
            event_id="evt-waiting-ai-republish",
            entity_type="table",
            entity_fqn="trino_prod.raw.logs",
        )

    assert duplicate["duplicate"] is True
    assert duplicate["status"] == "WAITING_AI"
    assert duplicate["execution_id"] == str(existing.id)
    assert duplicate["generation"] == existing.generation
    assert duplicate["ai_handoff_republished"] is True
    assert session.query(ClassificationExecution).count() == 1
    assert session.query(ClassificationExecution).one().status == "WAITING_AI"
    assert session.query(ClassificationExecution).one().generation == existing.generation
    assert mock_ai_task.delay.call_count == 2
    mock_ai_task.delay.assert_called_with(
        execution_id=str(existing.id),
        generation=existing.generation,
    )
    assert mock_om_client.apply_confirmed_tags.call_count == 0


def test_completed_duplicate_does_not_republish_ai_handoff(session) -> None:
    from app.repositories.classification_execution import ClassificationExecutionRepository
    from app.services.classification_rule_catalog import ClassificationRuleCatalogService

    rule_doc = {
        "version": "completed-duplicate",
        "rules": [
            {
                "id": "rule_unused",
                "target": "column",
                "when": {"name_exact": ["unused_column_name"]},
                "tag": "PII.Email",
                "auto_apply": True,
            }
        ],
    }
    ClassificationRuleCatalogService(session).import_json(
        json.dumps(rule_doc).encode(),
        filename="rules-completed-duplicate.json",
        actor_id="admin",
        actor_name="Admin",
        activate=True,
    )
    existing = ClassificationExecutionRepository(session).create(
        event_id="evt-completed-duplicate",
        entity_type="table",
        entity_fqn="trino_prod.raw.logs",
        generation=1,
        status="COMPLETED",
        outcome="MATCH",
    )
    session.commit()

    mock_om_client = MagicMock(spec=OpenMetadataClient, unsafe=True)
    with patch("app.tasks.classification.OpenMetadataClient", return_value=mock_om_client), \
         patch("app.tasks.classification.ai_classify_entity") as mock_ai_task, \
         patch("app.tasks.classification.SessionLocal", return_value=session):

        duplicate = classify_entity(
            event_id="evt-completed-duplicate",
            entity_type="table",
            entity_fqn="trino_prod.raw.logs",
        )

    assert duplicate == {
        "status": "COMPLETED",
        "outcome": "MATCH",
        "execution_id": str(existing.id),
        "generation": 1,
        "duplicate": True,
        "ai_handoff_republished": False,
    }
    assert session.query(ClassificationExecution).count() == 1
    mock_ai_task.delay.assert_not_called()
    mock_om_client.get_entity.assert_not_called()


# -----------------------------------------------------------------------------
# Integration D — OM -> Ranger TAG_SYNC, Apply & Post-Apply Read-Back Verification
# -----------------------------------------------------------------------------
def test_integration_d_tag_sync_reconciles_and_verifies_post_apply_readback(session) -> None:
    mock_om_client = MagicMock(spec=OpenMetadataClient, unsafe=True)
    mock_om_client.list_confirmed_table_tag_snapshots.return_value = [
        {
            "entity_type": "table",
            "entity_fqn": "trino_prod.sales.customers",
            "entity_tags": ["Sensitivity.Confidential"],
            "field_tags": {"columns.email": ["PII.Email"]},
        }
    ]

    mock_tag_store = create_autospec(RangerTagStoreClient, instance=True)
    mock_tag_store.dry_run = False
    mock_tag_store.read_actual_service_state.side_effect = [
        set(),
        {
            ("trino_prod.sales.customers", "$entity", "Sensitivity.Confidential"),
            ("trino_prod.sales.customers", "columns.email", "PII.Email"),
        },
    ]
    mock_tag_store.compare_service_state.side_effect = [False, True]
    mock_tag_store.compare_state.return_value = True
    mock_tag_store.read_actual_state.return_value = {
        ("$entity", "Sensitivity.Confidential"),
        ("columns.email", "PII.Email"),
    }
    mock_tag_store.reconcile_assignments.return_value = {
        "tags_reconciled": 2,
        "status": "applied",
    }
    mock_tag_store.remove_stale_service_assignments.return_value = []

    with patch("app.tasks.tag_sync.OpenMetadataClient", return_value=mock_om_client), \
         patch("app.tasks.tag_sync.RangerTagStoreClient", return_value=mock_tag_store), \
         patch("app.tasks.tag_sync.SessionLocal", return_value=session):

        result = sync_tags_to_ranger(
            entity_type="table",
            entity_fqn="trino_prod.sales.customers",
        )

        assert result["status"] == "SYNCHRONIZED"
        assert result["entity_fqn"] == "trino_prod.sales.customers"

        mock_tag_store.reconcile_assignments.assert_called_once()
        assert mock_tag_store.read_actual_service_state.call_count == 2
        mock_tag_store.read_actual_state.assert_called_once_with(
            "trino_prod.sales.customers"
        )
