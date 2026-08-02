from __future__ import annotations

from pathlib import Path

from app.core.config import Settings
from app.models.enums import ReconciliationAction
from app.services.policy_sync import PolicySyncService


class FakeRanger:
    def __init__(self) -> None:
        self.dry_run = True
        self.reconciled: list[str] = []
        self.removal_candidates: list[str] = []

    def reconcile(self, policy) -> dict:
        self.reconciled.append(policy.name)
        return {
            "action": ReconciliationAction.DRY_RUN.value,
            "desired_hash": f"desired:{policy.name}",
            "observed_hash": None,
            "policy_id": None,
            "document": {},
        }

    def reconcile_removal(self, policy_name: str, allow_delete: bool = False):
        self.removal_candidates.append(policy_name)
        if policy_name.endswith("-work_email"):
            return {
                "action": ReconciliationAction.DRY_RUN.value,
                "desired_hash": "disabled-hash",
                "observed_hash": "old-hash",
                "policy_id": "101",
                "document": {},
            }
        return None


def test_policy_sync_uses_all_live_fields_to_remove_stale_column_policy(session) -> None:
    settings = Settings(
        _env_file=None,
        policy_mappings_path=Path("config/policies.yaml"),
        ranger_service_name="dev_trino",
        ranger_allow_policy_delete=False,
    )
    ranger = FakeRanger()

    with session.begin():
        result = PolicySyncService(session, settings, ranger).sync(
            entity_fqn="hive.sales.customers",
            tags=["PII.Email"],
            field_paths={"PII.Email": ["columns.email"]},
            all_field_paths=["columns.email", "columns.work_email"],
            classification_run_id=None,
            correlation_id="corr",
        )

    assert "dg-pii-email-hive.sales.customers-email" in ranger.reconciled
    assert "dg-pii-email-hive.sales.customers-work_email" in ranger.removal_candidates
    assert any(
        item["action"] == ReconciliationAction.DRY_RUN.value
        for item in result["reconciliations"]
    )
