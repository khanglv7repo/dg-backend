"""Unit tests for production Ranger Tag Store desired vs observed semantic convergence comparison."""
from __future__ import annotations

from unittest.mock import patch, create_autospec
from app.clients.openmetadata import OpenMetadataClient
from app.clients.ranger_tags import RangerTagStoreClient
from app.core.errors import ExternalSystemError
from app.models.tag_sync_state import TagSyncState
from app.services.tag_sync_reconciliation import RangerTagSyncReconciliationService


def test_production_ranger_tag_compare_state_exact_match() -> None:
    client = RangerTagStoreClient(
        base_url="http://ranger:6080",
        username="admin",
        password="password",
        resource_service_name="dg_trino",
        dry_run=True,
    )
    desired = {("$entity", "Sensitivity.Confidential"), ("columns.email", "PII.Email")}
    actual = {("$entity", "Sensitivity.Confidential"), ("columns.email", "PII.Email")}

    assert client.compare_state(desired, actual) is True


def test_production_ranger_tag_compare_state_missing_tag_fails() -> None:
    client = RangerTagStoreClient(
        base_url="http://ranger:6080",
        username="admin",
        password="password",
        resource_service_name="dg_trino",
        dry_run=True,
    )
    desired = {("$entity", "Sensitivity.Confidential"), ("columns.email", "PII.Email")}
    actual = {("$entity", "Sensitivity.Confidential")}  # PII.Email missing!

    assert client.compare_state(desired, actual) is False


def test_production_ranger_verify_convergence_readback_failure() -> None:
    client = RangerTagStoreClient(
        base_url="http://ranger:6080",
        username="admin",
        password="password",
        resource_service_name="dg_trino",
        dry_run=False,
    )

    with patch.object(client, "read_actual_state") as mock_read:
        # Read-back returns missing mapping
        mock_read.return_value = {("$entity", "Sensitivity.Confidential")}

        converged = client.verify_convergence(
            entity_fqn="financial_db.public.customer",
            entity_tags=["Sensitivity.Confidential"],
            field_tags={"columns.email": ["PII.Email"]},
        )
        assert converged is False, "Production verify_convergence MUST return False when read-back misses desired mapping!"


def test_full_snapshot_reconciliation_reads_after_apply(session) -> None:
    om_client = create_autospec(OpenMetadataClient, instance=True)
    tag_store = create_autospec(RangerTagStoreClient, instance=True)
    tag_store.dry_run = False
    tag_store.read_actual_service_state.side_effect = [
        set(),
        {("hive.sales.customers", "columns.phone", "PII.Phone")},
    ]
    tag_store.compare_service_state.side_effect = [False, True]
    tag_store.compare_state.return_value = True
    tag_store.read_actual_state.return_value = {("columns.phone", "PII.Phone")}
    tag_store.reconcile_assignments.return_value = {"action": "SYNC"}
    tag_store.remove_stale_service_assignments.return_value = []
    om_client.list_confirmed_table_tag_snapshots.return_value = [
        {
            "entity_type": "table",
            "entity_fqn": "hive.sales.customers",
            "entity_tags": [],
            "field_tags": {"columns.phone": ["PII.Phone"]},
        }
    ]

    result = RangerTagSyncReconciliationService(
        session,
        om_client,
        tag_store,
    ).synchronize_full_snapshot()

    assert result["status"] == "SYNCHRONIZED"
    assert tag_store.read_actual_service_state.call_count == 2
    tag_store.read_actual_state.assert_called_once_with("hive.sales.customers")
    tag_store.reconcile_assignments.assert_called_once()


def test_full_snapshot_reconciliation_does_not_record_sync_when_entity_readback_has_extra_tag(session) -> None:
    om_client = create_autospec(OpenMetadataClient, instance=True)
    tag_store = create_autospec(RangerTagStoreClient, instance=True)
    entity_fqn = "financial_postgres.financial_db.crm.customers"
    desired_service_state = {(entity_fqn, "columns.email", "PII.Email")}
    tag_store.dry_run = False
    tag_store.read_actual_service_state.side_effect = [
        {
            (entity_fqn, "columns.email", "PII.Email"),
            (entity_fqn, "columns.email", "PII.Phone"),
        },
        desired_service_state,
    ]
    tag_store.compare_service_state.side_effect = [False, True]
    tag_store.compare_state.return_value = False
    tag_store.read_actual_state.return_value = {
        ("columns.email", "PII.Email"),
        ("columns.email", "PII.Phone"),
    }
    tag_store.reconcile_assignments.return_value = {"action": "SYNC"}
    tag_store.remove_stale_service_assignments.return_value = [
        {
            "entity_fqn": entity_fqn,
            "field_path": "columns.email",
            "tag": "PII.Phone",
            "map_id": "41",
        }
    ]
    om_client.list_confirmed_table_tag_snapshots.return_value = [
        {
            "entity_type": "table",
            "entity_fqn": entity_fqn,
            "entity_tags": [],
            "field_tags": {"columns.email": ["PII.Email"]},
        }
    ]

    service = RangerTagSyncReconciliationService(session, om_client, tag_store)

    try:
        service.synchronize_full_snapshot()
    except ExternalSystemError:
        pass
    else:
        raise AssertionError("expected entity read-back convergence failure")

    assert session.query(TagSyncState).count() == 0
    tag_store.remove_stale_service_assignments.assert_called_once_with(
        expected=desired_service_state,
        resource_scope={(entity_fqn, "columns.email")},
        entity_scope={entity_fqn},
    )
    tag_store.read_actual_state.assert_called_once_with(entity_fqn)
