"""Unit tests for production Ranger Tag Store desired vs observed semantic convergence comparison."""
from __future__ import annotations

from unittest.mock import patch, create_autospec
from app.clients.openmetadata import OpenMetadataClient
from app.clients.ranger_tags import RangerTagStoreClient
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
    tag_store.reconcile_assignments.assert_called_once()
