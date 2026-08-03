from __future__ import annotations

from app.core.config import Settings
from app.models.enums import ReconciliationAction
from app.services.policy_catalog import PolicyCatalogService
from app.services.policy_sync import RangerPolicyCatalogSyncService


class FakePolicyClient:
    def __init__(self) -> None:
        self.documents = []

    def reconcile_document(self, *, policy_key, document):
        self.documents.append((policy_key, document))
        return {
            "action": ReconciliationAction.CREATE.value,
            "desired_hash": "desired",
            "observed_hash": None,
            "policy_id": "1",
            "document": document,
        }


class FakeTagStore:
    def __init__(self) -> None:
        self.definitions = []

    def ensure_tag_definition(self, tag_type):
        self.definitions.append(tag_type)
        return {"name": tag_type}


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        ranger_service_name="dev_trino",
        ranger_tag_service_name="dev_tag",
        ranger_dry_run=False,
    )


def test_db_catalog_syncs_tag_and_resource_policies(session) -> None:
    tag_document = {
        "service": "dev_tag",
        "serviceType": "tag",
        "name": "pii-phone",
        "resources": {
            "tag": {
                "values": ["PII.Phone"],
                "isExcludes": False,
                "isRecursive": False,
            }
        },
        "policyItems": [],
    }
    resource_document = {
        "service": "dev_trino",
        "serviceType": "trino",
        "name": "queryid",
        "resources": {
            "queryid": {
                "values": ["*"],
                "isExcludes": False,
                "isRecursive": False,
            }
        },
        "policyItems": [],
    }

    with session.begin():
        catalog = PolicyCatalogService(session, _settings())
        catalog.import_document(tag_document, actor_id="test", actor_name="Test")
        catalog.import_document(resource_document, actor_id="test", actor_name="Test")

    tag_client = FakePolicyClient()
    resource_client = FakePolicyClient()
    tag_store = FakeTagStore()
    with session.begin():
        result = RangerPolicyCatalogSyncService(
            session,
            _settings(),
            {
                "dev_tag": tag_client,
                "dev_trino": resource_client,
            },
            tag_store,
        ).sync()

    assert result["policies"] == 2
    assert tag_store.definitions == ["PII.Phone"]
    assert tag_client.documents[0][0] == "dev_tag:pii-phone"
    assert resource_client.documents[0][0] == "dev_trino:queryid"
