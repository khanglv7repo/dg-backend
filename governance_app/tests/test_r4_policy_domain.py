from __future__ import annotations

from copy import deepcopy
from unittest.mock import create_autospec

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.clients.ranger import RangerClient
from app.core.config import Settings
from app.core.errors import ValidationError
from app.schemas.data_access_policy import LogicalDataAccessPolicy
from app.services.data_access_policy import DataAccessPolicyService


def logical_policy(**overrides) -> dict:
    value = {
        "subjects": [
            {"type": "USER", "name": "alice"},
            {"type": "GROUP", "name": "pii_readers"},
        ],
        "resource": {
            "catalog": "dev",
            "schema": "sales",
            "table": "customer",
        },
        "access": {" SELECT ": "allow", "insert": "DENY"},
        "masks": {"phone": "mask"},
        "row_filter": " region = 'VN' ",
    }
    value.update(overrides)
    return value


def settings() -> Settings:
    return Settings(
        app_env="test",
        ranger_enabled=True,
        ranger_service_name="dev_trino",
        ranger_dry_run=False,
    )


def ranger_with_subjects(*, alice: bool = True, group: bool = True):
    ranger = create_autospec(RangerClient, instance=True)
    ranger.user_exists.side_effect = lambda name: alice if name == "alice" else False
    ranger.group_exists.side_effect = lambda name: group if name == "pii_readers" else False
    return ranger


def test_valid_logical_policy_normalizes_contract() -> None:
    parsed = LogicalDataAccessPolicy.model_validate(logical_policy())
    assert parsed.access == {"select": "ALLOW", "insert": "DENY"}
    assert parsed.masks == {"phone": "MASK"}
    assert parsed.row_filter == "region = 'VN'"
    assert [subject.type.value for subject in parsed.subjects] == ["USER", "GROUP"]


@pytest.mark.parametrize(
    "patch",
    [
        {"subjects": []},
        {"subjects": [{"type": "ROLE", "name": "analyst"}]},
        {"subjects": [{"type": "USER", "name": "   "}]},
        {"resource": {"catalog": "", "schema": "sales", "table": "customer"}},
        {"access": {"select": "MAYBE"}, "masks": {}, "row_filter": None},
        {"access": {"made_up_operation": "ALLOW"}, "masks": {}, "row_filter": None},
        {"access": {}, "masks": {"   ": "MASK"}, "row_filter": None},
        {"access": {}, "masks": {}, "row_filter": "   "},
    ],
)
def test_invalid_logical_policy_rejected(patch: dict) -> None:
    value = logical_policy()
    value.update(patch)
    with pytest.raises(PydanticValidationError):
        LogicalDataAccessPolicy.model_validate(value)



def test_native_ranger_json_is_not_a_valid_r4_domain_input() -> None:
    native_ranger = {
        "service": "dev_trino",
        "name": "legacy-native",
        "policyType": 0,
        "resources": {"table": {"values": ["customer"]}},
        "policyItems": [],
    }
    with pytest.raises(PydanticValidationError):
        LogicalDataAccessPolicy.model_validate(native_ranger)

def test_policy_can_be_mask_or_row_filter_only() -> None:
    mask_only = logical_policy(access={}, row_filter=None)
    row_only = logical_policy(access={}, masks={})
    assert LogicalDataAccessPolicy.model_validate(mask_only).masks
    assert LogicalDataAccessPolicy.model_validate(row_only).row_filter


def test_empty_policy_semantics_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        LogicalDataAccessPolicy.model_validate(
            logical_policy(access={}, masks={}, row_filter=None)
        )


def test_version_increment_immutability_one_active_and_rollback(session) -> None:
    ranger = ranger_with_subjects()
    service = DataAccessPolicyService(
        session,
        settings(),
        ranger_client=ranger,
    )
    original_v1 = logical_policy(masks={}, row_filter=None)
    original_v2 = logical_policy(
        access={"select": "ALLOW"},
        masks={"phone": "MASK"},
        row_filter=None,
    )

    with session.begin():
        v1 = service.create_version(
            policy_key="sales.customer.read",
            logical_policy=original_v1,
            actor_id="admin",
            actor_name="Admin",
        )
        v2 = service.create_version(
            policy_key="sales.customer.read",
            logical_policy=original_v2,
            actor_id="admin",
            actor_name="Admin",
        )
    assert (v1.version, v2.version) == (1, 2)
    assert v1.status == "DRAFT"
    assert v2.status == "DRAFT"

    v1_snapshot = deepcopy(v1.logical_policy)
    with session.begin():
        service.activate_version(
            policy_key=v1.policy_key,
            version=1,
            actor_id="admin",
            actor_name="Admin",
        )
    assert v1.status == "ACTIVE"

    with session.begin():
        service.activate_version(
            policy_key=v2.policy_key,
            version=2,
            actor_id="admin",
            actor_name="Admin",
        )
    assert v1.status == "INACTIVE"
    assert v2.status == "ACTIVE"
    assert v1.logical_policy == v1_snapshot

    with session.begin():
        rolled_back, changed = service.rollback(
            policy_key=v2.policy_key,
            target_version=None,
            actor_id="admin",
            actor_name="Admin",
        )
    assert changed is True
    assert rolled_back.id == v1.id
    assert v1.status == "ACTIVE"
    assert v2.status == "INACTIVE"
    assert v1.logical_policy == v1_snapshot

    v1.logical_policy = {"subjects": []}
    with pytest.raises(ValueError, match="immutable"):
        session.flush()
    session.rollback()


def test_missing_user_blocks_activation_before_projection_mutation(session) -> None:
    ranger = ranger_with_subjects(alice=False, group=True)
    service = DataAccessPolicyService(session, settings(), ranger_client=ranger)
    with session.begin():
        version = service.create_version(
            policy_key="missing-user",
            logical_policy=logical_policy(masks={}, row_filter=None),
            actor_id="admin",
            actor_name="Admin",
        )

    with pytest.raises(ValidationError) as excinfo:
        with session.begin():
            service.activate_version(
                policy_key="missing-user",
                version=version.version,
                actor_id="admin",
                actor_name="Admin",
            )
    assert excinfo.value.details["missing_subjects"] == [
        {"type": "USER", "name": "alice"}
    ]
    session.expire_all()
    assert service.get_version(policy_key="missing-user", version=1).status == "DRAFT"
    assert service.repository.list_projections(version.id) == []
    assert not hasattr(ranger, "create_user")
    assert not hasattr(ranger, "create_group")


def test_missing_group_blocks_activation(session) -> None:
    ranger = ranger_with_subjects(alice=True, group=False)
    service = DataAccessPolicyService(session, settings(), ranger_client=ranger)
    with session.begin():
        version = service.create_version(
            policy_key="missing-group",
            logical_policy=logical_policy(masks={}, row_filter=None),
            actor_id="admin",
            actor_name="Admin",
        )

    with pytest.raises(ValidationError):
        with session.begin():
            service.activate_version(
                policy_key="missing-group",
                version=version.version,
                actor_id="admin",
                actor_name="Admin",
            )
    assert service.repository.list_projections(version.id) == []
