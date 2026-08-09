"""Contract tests for RangerTagStoreClient using create_autospec.

Guarantees tests fail if code calls a non-existent method signature on RangerTagStoreClient.
"""
from __future__ import annotations

from unittest.mock import create_autospec

from app.clients.ranger_tags import RangerTagStoreClient
from app.tasks.tag_sync import sync_tags_to_ranger


def test_ranger_tag_store_client_contract_autospec() -> None:
    """Verify that RangerTagStoreClient public signatures are strictly adhered to by tag_sync task."""
    mock_ranger_tags = create_autospec(RangerTagStoreClient, instance=True)

    # Calling legitimate method succeeds
    mock_ranger_tags.reconcile_assignments(
        entity_fqn="financial_db.public.customer",
        entity_tags=["PII.Sensitive"],
        field_tags={"columns.email": ["PII.Email"]},
    )
    mock_ranger_tags.reconcile_assignments.assert_called_once_with(
        entity_fqn="financial_db.public.customer",
        entity_tags=["PII.Sensitive"],
        field_tags={"columns.email": ["PII.Email"]},
    )


def test_ranger_autospec_rejects_invented_methods() -> None:
    """Demonstrate that create_autospec prevents invoking non-existent Ranger adapter methods."""
    mock_ranger_tags = create_autospec(RangerTagStoreClient, instance=True)
    try:
        mock_ranger_tags.non_existent_ranger_method()
        assert False, "Should have raised AttributeError for non-existent method"
    except AttributeError:
        pass
