"""R3 Integration Test Suite crossing real production boundaries."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, create_autospec, patch
import pytest

from app.clients.openmetadata import OpenMetadataClient
from app.clients.ranger_tags import RangerTagStoreClient
from app.core.config import Settings
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
