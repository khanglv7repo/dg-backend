from pathlib import Path

import pytest

from app.core.errors import ConfigurationError
from app.rules.policy_mapping import PolicyMappingResolver


def test_policy_catalog_renders_static_ranger_tag_policy() -> None:
    resolver = PolicyMappingResolver.from_path(Path("config/policies.yaml"))

    policies = resolver.resolve_all(service="dev_tag", tags=["PII.Email"])

    assert len(policies) == 1
    policy = policies[0]
    assert policy.name == "dg-tag-pii-email"
    assert policy.service == "dev_tag"
    assert policy.resources == {"tag": ["PII.Email"]}
    assert policy.groups == ["pii_readers"]
    assert policy.accesses == ["trino:select"]


def test_policy_catalog_does_not_need_asset_or_field() -> None:
    resolver = PolicyMappingResolver.from_path(Path("config/policies.yaml"))

    policies = resolver.resolve_all(service="dev_tag")

    assert {policy.resources["tag"][0] for policy in policies} == {
        "PII.Email",
        "PII.Phone",
        "PII.NationalIdentifier",
        "PII.PaymentCard",
    }
    assert all("${entity_fqn}" not in policy.name for policy in policies)
    assert all("${field_name}" not in policy.name for policy in policies)


def test_policy_catalog_rejects_old_per_asset_placeholders() -> None:
    with pytest.raises(ConfigurationError, match="per-asset Ranger policy placeholders"):
        PolicyMappingResolver(
            {
                "tag_policies": [
                    {
                        "tag": "PII.Email",
                        "name": "dg-${entity_fqn}-${field_name}",
                        "groups": ["pii_readers"],
                    }
                ]
            }
        )


def test_policy_catalog_rejects_unqualified_tag_access_type() -> None:
    with pytest.raises(ConfigurationError, match="namespaced access types"):
        PolicyMappingResolver(
            {
                "tag_policies": [
                    {
                        "tag": "PII.Email",
                        "name": "dg-tag-pii-email",
                        "groups": ["pii_readers"],
                        "accesses": ["select"],
                    }
                ]
            }
        )
