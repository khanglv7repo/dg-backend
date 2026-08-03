from __future__ import annotations

from pathlib import Path

from app.core.config import Settings
from app.models.enums import ReconciliationAction
from app.services.policy_sync import (
    RangerTagAssignmentService,
    RangerTagPolicyCatalogService,
)


class FakePolicyClient:
    def __init__(self) -> None:
        self.dry_run = False
        self.reconciled = []

    def reconcile(self, policy):
        self.reconciled.append(policy)
        return {
            "action": ReconciliationAction.CREATE.value,
            "desired_hash": "hash",
            "observed_hash": None,
            "policy_id": "1",
            "document": policy.ranger_document(),
        }

    def list_policies(self):
        return []

    def reconcile_removal(self, policy_name, allow_delete=False):
        raise AssertionError(f"unexpected stale policy: {policy_name}")


class FakeTagStore:
    def __init__(self) -> None:
        self.dry_run = False
        self.definitions = []
        self.synced = None

    def ensure_tag_definition(self, tag_type):
        self.definitions.append(tag_type)
        return {"name": tag_type}

    def reconcile_assignments(self, **kwargs):
        self.synced = kwargs
        return {"action": "SYNC", **kwargs}


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        policy_mappings_path=Path("config/policies.yaml"),
        ranger_service_name="dev_trino",
        ranger_tag_service_name="dev_tag",
        ranger_dry_run=False,
    )


def test_flow_a_reconciles_policy_catalog_without_openmetadata() -> None:
    policy_client = FakePolicyClient()
    tag_store = FakeTagStore()

    result = RangerTagPolicyCatalogService(
        _settings(),
        policy_client,
        tag_store,
    ).reconcile()

    assert result["tag_service"] == "dev_tag"
    assert result["resource_service"] == "dev_trino"
    assert result["policies"] == 4
    assert set(tag_store.definitions) == {
        "PII.Email",
        "PII.Phone",
        "PII.NationalIdentifier",
        "PII.PaymentCard",
    }
    assert all(
        policy.resources.keys() == {"tag"}
        for policy in policy_client.reconciled
    )


def test_flow_b_syncs_confirmed_tags_without_policy_lookup(session) -> None:
    tag_store = FakeTagStore()

    with session.begin():
        result = RangerTagAssignmentService(
            session,
            _settings(),
            tag_store,
        ).sync(
            entity_type="table",
            entity_fqn="hive.sales.customers",
            entity_tags=[],
            field_tags={
                "columns.email": ["PII.Email"],
                "columns.mobile": ["PII.Phone"],
            },
            classification_run_id=None,
            correlation_id="corr",
        )

    assert result["action"] == "SYNC"
    assert tag_store.synced == {
        "entity_fqn": "hive.sales.customers",
        "entity_tags": [],
        "field_tags": {
            "columns.email": ["PII.Email"],
            "columns.mobile": ["PII.Phone"],
        },
    }
