from __future__ import annotations

from app.schemas.data_access_policy import LogicalDataAccessPolicy
from app.services.policy_compiler import PolicyCompiler


def policy() -> LogicalDataAccessPolicy:
    return LogicalDataAccessPolicy.model_validate(
        {
            "subjects": [
                {"type": "USER", "name": "alice"},
                {"type": "GROUP", "name": "pii_readers"},
            ],
            "resource": {
                "catalog": "dev",
                "schema": "sales",
                "table": "customer",
            },
            "access": {"select": "ALLOW", "insert": "DENY"},
            "masks": {"phone": "MASK", "email": "MASK"},
            "row_filter": "region = 'VN'",
        }
    )


def test_compiler_access_preserves_allow_and_explicit_deny() -> None:
    compiled = PolicyCompiler(ranger_service_name="dev_trino").compile(
        policy_key="sales.customer",
        version=1,
        logical_policy=policy(),
    )
    access = next(value for value in compiled if value.projection_type == "ACCESS")
    document = access.document
    assert document["policyType"] == 0
    assert document["resources"] == {
        "catalog": {"values": ["dev"], "isExcludes": False, "isRecursive": False},
        "schema": {"values": ["sales"], "isExcludes": False, "isRecursive": False},
        "table": {"values": ["customer"], "isExcludes": False, "isRecursive": False},
    }
    assert document["policyItems"][0]["accesses"] == [
        {"type": "select", "isAllowed": True}
    ]
    assert document["denyPolicyItems"][0]["accesses"] == [
        {"type": "insert", "isAllowed": True}
    ]
    assert document["policyItems"][0]["users"] == ["alice"]
    assert document["policyItems"][0]["groups"] == ["pii_readers"]


def test_compiler_mask_is_separate_policy_type_1_and_one_per_column() -> None:
    compiled = PolicyCompiler(ranger_service_name="dev_trino").compile(
        policy_key="sales.customer",
        version=1,
        logical_policy=policy(),
    )
    masks = [value for value in compiled if value.projection_type == "MASK"]
    assert len(masks) == 2
    assert {value.document["resources"]["column"]["values"][0] for value in masks} == {
        "email",
        "phone",
    }
    for projection in masks:
        document = projection.document
        assert document["policyType"] == 1
        assert document["dataMaskPolicyItems"][0]["accesses"] == [
            {"type": "select", "isAllowed": True}
        ]
        assert document["dataMaskPolicyItems"][0]["dataMaskInfo"] == {
            "dataMaskType": "MASK"
        }
        assert "policyItems" not in document
        assert "rowFilterPolicyItems" not in document


def test_compiler_row_filter_is_separate_policy_type_2() -> None:
    compiled = PolicyCompiler(ranger_service_name="dev_trino").compile(
        policy_key="sales.customer",
        version=1,
        logical_policy=policy(),
    )
    row_filter = next(
        value for value in compiled if value.projection_type == "ROW_FILTER"
    )
    document = row_filter.document
    assert document["policyType"] == 2
    assert set(document["resources"]) == {"catalog", "schema", "table"}
    assert document["rowFilterPolicyItems"][0]["accesses"] == [
        {"type": "select", "isAllowed": True}
    ]
    assert document["rowFilterPolicyItems"][0]["rowFilterInfo"] == {
        "filterExpr": "region = 'VN'"
    }
    assert "dataMaskPolicyItems" not in document


def test_projection_set_identity_and_checksums_are_deterministic() -> None:
    compiler = PolicyCompiler(ranger_service_name="dev_trino")
    first = compiler.compile(
        policy_key="sales.customer",
        version=1,
        logical_policy=policy(),
    )
    second = compiler.compile(
        policy_key="sales.customer",
        version=2,
        logical_policy=policy(),
    )
    assert len(first) == 4
    assert [item.ranger_policy_name for item in first] == [
        item.ranger_policy_name for item in second
    ]
    assert [item.desired_checksum for item in first] == [
        item.desired_checksum for item in second
    ]
    assert len({item.ranger_policy_name for item in first}) == 4
