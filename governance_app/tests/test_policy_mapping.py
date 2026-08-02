from pathlib import Path

from app.clients.ranger import canonical_hash, normalize_policy
from app.rules.policy_mapping import PolicyMappingResolver


def test_policy_mapping_renders_entity_and_column_placeholders() -> None:
    resolver = PolicyMappingResolver.from_path(Path("config/policies.yaml"))

    policies = resolver.resolve_all(
        tags=["PII.Email", "Sensitivity.Confidential"],
        entity_fqn="hive.sales.customers",
        field_paths={"PII.Email": ["columns.email"]},
        service="trino",
    )

    assert len(policies) == 1
    policy = policies[0]
    assert policy.resources["column"] == ["email"]
    assert policy.resources["table"] == ["hive.sales.customers"]
    assert policy.verification_cases[0].sql == "SELECT email FROM hive.sales.customers LIMIT 1"


def test_policy_mapping_creates_one_policy_per_tagged_column() -> None:
    resolver = PolicyMappingResolver.from_path(Path("config/policies.yaml"))

    policies = resolver.resolve_all(
        tags=["PII.Email"],
        entity_fqn="hive.sales.customers",
        field_paths={"PII.Email": ["columns.work_email", "columns.personal_email"]},
        service="trino",
    )

    assert [policy.resources["column"][0] for policy in policies] == [
        "personal_email",
        "work_email",
    ]
    assert len({policy.policy_key for policy in policies}) == 2


def test_ranger_normalization_strips_backend_marker() -> None:
    desired = {
        "service": "trino",
        "name": "policy",
        "description": "Base description",
        "isEnabled": True,
        "resources": {},
        "policyItems": [],
    }
    live = {
        **desired,
        "description": (
            "Base description | managed-by=dg-backend;policy-key=k;desired-sha256=abc"
        ),
        "id": 42,
    }
    assert normalize_policy(desired) == normalize_policy(live)
    assert canonical_hash(normalize_policy(desired) or {}) == canonical_hash(
        normalize_policy(live) or {}
    )


def test_policy_mapping_allows_classification_tags_without_enforcement_mapping() -> None:
    resolver = PolicyMappingResolver.from_path(Path("config/policies.yaml"))

    policies = resolver.resolve_all(
        tags=["Sensitivity.Confidential"],
        entity_fqn="hive.sales.customers",
        field_paths={},
        service="trino",
    )

    assert policies == []
