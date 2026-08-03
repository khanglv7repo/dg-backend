from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.errors import ConfigurationError
from app.services.policy_catalog import PolicyCatalogService


TAG_POLICY = {
    "isEnabled": True,
    "service": "dev_tag",
    "name": "dg-tag-pii-phone",
    "policyType": 0,
    "policyPriority": 0,
    "description": (
        "Allow governed PII readers to select resources tagged PII.Phone. "
        "| managed-by=dg-backend;policy-key=tag-policy:PII.Phone;desired-sha256=abc"
    ),
    "isAuditEnabled": True,
    "resources": {
        "tag": {
            "values": ["PII.Phone"],
            "isExcludes": False,
            "isRecursive": False,
        }
    },
    "policyItems": [
        {
            "accesses": [{"type": "trino:select", "isAllowed": True}],
            "groups": ["pii_readers"],
            "delegateAdmin": False,
        }
    ],
    "serviceType": "tag",
    "isDenyAllElse": False,
}

RESOURCE_POLICY = {
    "isEnabled": True,
    "service": "dev_trino",
    "name": "all - queryid",
    "policyType": 0,
    "policyPriority": 0,
    "description": "Policy for all - queryid",
    "isAuditEnabled": True,
    "resources": {
        "queryid": {
            "values": ["*"],
            "isExcludes": False,
            "isRecursive": False,
        }
    },
    "policyItems": [
        {
            "accesses": [{"type": "execute", "isAllowed": True}],
            "users": ["trino"],
            "delegateAdmin": True,
        }
    ],
    "serviceType": "trino",
    "isDenyAllElse": False,
}


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        ranger_service_name="dev_trino",
        ranger_tag_service_name="dev_tag",
        policy_mappings_path=Path("config/policies.yaml"),
    )


def test_imports_native_tag_policy_and_strips_runtime_marker(session) -> None:
    with session.begin():
        policy, created, changed = PolicyCatalogService(
            session,
            _settings(),
        ).import_document(
            TAG_POLICY,
            actor_id="test",
            actor_name="Test",
        )

    assert created is True
    assert changed is True
    assert policy.policy_key == "dev_tag:dg-tag-pii-phone"
    assert policy.policy_kind == "TAG"
    assert "managed-by=dg-backend" not in policy.document["description"]
    assert policy.document["resources"]["tag"]["values"] == ["PII.Phone"]


def test_reimport_updates_same_policy_revision(session) -> None:
    service = PolicyCatalogService(session, _settings())
    with session.begin():
        first, _, _ = service.import_document(
            RESOURCE_POLICY,
            actor_id="test",
            actor_name="Test",
        )
    assert first.revision == 1

    changed_document = {**RESOURCE_POLICY, "description": "Updated description"}
    with session.begin():
        second, created, changed = service.import_document(
            changed_document,
            actor_id="test",
            actor_name="Test",
        )

    assert created is False
    assert changed is True
    assert second.id == first.id
    assert second.revision == 2


def test_rejects_policy_for_unknown_ranger_service(session) -> None:
    document = {**RESOURCE_POLICY, "service": "other_service"}
    with session.begin(), pytest.raises(ConfigurationError, match="unsupported Ranger service"):
        PolicyCatalogService(session, _settings()).import_document(
            document,
            actor_id="test",
            actor_name="Test",
        )
