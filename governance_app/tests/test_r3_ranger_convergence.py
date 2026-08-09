"""Unit tests for production Ranger Tag Store desired vs observed semantic convergence comparison."""
from __future__ import annotations

from unittest.mock import patch, create_autospec
from app.clients.ranger_tags import RangerTagStoreClient


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
