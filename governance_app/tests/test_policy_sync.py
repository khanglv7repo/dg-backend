from __future__ import annotations

from app.core.config import Settings
from app.services.policy_sync import RangerTagAssignmentService


class FakeTagStore:
    def __init__(self) -> None:
        self.dry_run = False
        self.synced = None

    def reconcile_assignments(self, **kwargs):
        self.synced = kwargs
        return {"action": "SYNC", **kwargs}


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        ranger_service_name="dev_trino",
        ranger_tag_service_name="dev_tag",
        ranger_dry_run=False,
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
