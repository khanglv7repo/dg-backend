from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.core.config import Settings
from app.core.errors import ConflictError, ExternalSystemError, ValidationError
from app.services.dq_service import (
    DQService,
    build_natural_key_hash,
    build_stable_test_slot_id,
)


def settings() -> Settings:
    return Settings(app_env="test")


def staged(session, **overrides) -> tuple[DQService, dict]:
    values = {
        "target_asset_fqn": "financial.crm.customers",
        "test_definition_fqn": "columnValuesToBeNotNull",
        "parameter_values": {},
        "rule_id": "rule-1",
        "test_key": None,
        "column_name": "email",
        "worker_id": "agent-1",
        "rationale": "email must exist",
    }
    values.update(overrides)
    service = DQService(session, settings())
    return service, service.create_staged_test_case(**values)


def approved(session, **overrides) -> tuple[DQService, dict]:
    service, result = staged(session, **overrides)
    return service, service.approve_staged_test_case(
        registry_id=result["id"],
        actor_id="operator-1",
    )


def test_build_stable_test_slot_id_uses_test_key_when_present() -> None:
    assert build_stable_test_slot_id(rule_id="r1", test_key="slot-a") == "r1::slot-a"
    assert build_stable_test_slot_id(rule_id="r1", test_key=None) == "r1"


def test_build_natural_key_hash_is_deterministic_and_prefixed() -> None:
    h1 = build_natural_key_hash(
        target_entity_fqn="a.b.c",
        test_definition_fqn="d",
        stable_test_slot_id="r1",
    )
    h2 = build_natural_key_hash(
        target_entity_fqn="a.b.c",
        test_definition_fqn="d",
        stable_test_slot_id="r1",
    )
    assert h1 == h2
    assert h1.startswith("dg_")
    assert len(h1) == len("dg_") + 32


def test_stage_requires_core_fields(session) -> None:
    service = DQService(session, settings())
    with pytest.raises(ValidationError):
        service.create_staged_test_case(
            target_asset_fqn="",
            test_definition_fqn="d",
            parameter_values={},
            rule_id="r1",
            test_key=None,
            column_name=None,
            worker_id="w1",
        )


def test_stage_is_backend_only_and_does_not_require_openmetadata(session) -> None:
    service, result = staged(session)

    assert result["status"] == "STAGED"
    assert result["om_testcase_id"] is None

    record = service.registry.get(result["id"])
    assert record.lifecycle_state == "STAGED"
    assert record.reservation_state == "RESERVED"
    assert record.om_testcase_id is None
    assert record.spec_payload["column_name"] == "email"


def test_identical_stage_retry_is_idempotent(session) -> None:
    service, first = staged(session)
    second = service.create_staged_test_case(
        target_asset_fqn="financial.crm.customers",
        test_definition_fqn="columnValuesToBeNotNull",
        parameter_values={},
        rule_id="rule-1",
        test_key=None,
        column_name="email",
        worker_id="agent-1",
        rationale="email must exist",
    )
    assert second["id"] == first["id"]
    assert second["status"] == "STAGED"


def test_same_natural_key_with_different_spec_is_conflict(session) -> None:
    service, _first = staged(session)
    with pytest.raises(ConflictError, match="different staged spec"):
        service.create_staged_test_case(
            target_asset_fqn="financial.crm.customers",
            test_definition_fqn="columnValuesToBeNotNull",
            parameter_values={"threshold": 10},
            rule_id="rule-1",
            test_key=None,
            column_name="email",
            worker_id="agent-1",
            rationale="changed semantics",
        )


def test_human_approval_is_backend_only_and_idempotent(session) -> None:
    service, stage = staged(session)
    first = service.approve_staged_test_case(
        registry_id=stage["id"],
        actor_id="operator-1",
    )
    second = service.approve_staged_test_case(
        registry_id=stage["id"],
        actor_id="operator-2",
    )

    assert first["status"] == second["status"] == "APPROVED"
    record = service.registry.get(stage["id"])
    assert record.approved_by == "operator-1"
    assert record.om_testcase_id is None


def test_materialization_requires_approval(session) -> None:
    service, stage = staged(session)
    service.om_client = MagicMock()

    with pytest.raises(ConflictError, match="cannot materialize"):
        service.materialize_approved_test_case(registry_id=stage["id"])
    service.om_client.create_test_case.assert_not_called()


def test_materialization_creates_then_requires_basic_suite_readback(session) -> None:
    service, approval = approved(session)
    om = MagicMock()
    om.find_test_case_by_entity_and_name.return_value = None
    om.build_entity_link.return_value = (
        "<#E::table::financial.crm.customers::columns::email>"
    )
    om.create_test_case.return_value = {
        "id": "om-1",
        "fullyQualifiedName": "financial.crm.customers.email.dg_abc",
    }
    om.get_test_case_by_name.return_value = {
        "id": "om-1",
        "name": "dg_abc",
        "fullyQualifiedName": "financial.crm.customers.email.dg_abc",
        "testSuite": {"id": "suite-1", "type": "testSuite"},
    }
    service.om_client = om

    result = service.materialize_approved_test_case(registry_id=approval["id"])

    assert result["status"] == "EXECUTABLE"
    assert result["om_testcase_id"] == "om-1"
    om.create_test_case.assert_called_once()
    record = service.registry.get(approval["id"])
    assert record.reservation_state == "CONFIRMED"
    assert record.lifecycle_state == "EXECUTABLE"


def test_materialization_reuses_existing_om_testcase_after_crash(session) -> None:
    service, approval = approved(session)
    om = MagicMock()
    om.find_test_case_by_entity_and_name.return_value = {
        "id": "om-existing",
        "name": service.registry.get(approval["id"]).natural_key_hash,
        "fullyQualifiedName": "financial.crm.customers.email.dg_existing",
        "testSuite": {"id": "suite-1", "type": "testSuite"},
    }
    service.om_client = om

    result = service.materialize_approved_test_case(registry_id=approval["id"])

    assert result["status"] == "EXECUTABLE"
    assert result["om_testcase_id"] == "om-existing"
    om.create_test_case.assert_not_called()


def test_materialization_without_basic_suite_never_marks_executable(session) -> None:
    service, approval = approved(session)
    om = MagicMock()
    om.find_test_case_by_entity_and_name.return_value = {
        "id": "om-1",
        "name": "dg_x",
        "fullyQualifiedName": "financial.crm.customers.email.dg_x",
        "testSuite": None,
    }
    service.om_client = om

    with pytest.raises(ExternalSystemError, match="Basic TestSuite"):
        service.materialize_approved_test_case(registry_id=approval["id"])

    assert service.registry.get(approval["id"]).lifecycle_state == "APPROVED"
