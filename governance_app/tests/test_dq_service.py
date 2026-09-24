"""Regression tests for the 2 real bugs found + fixed during TASK-08's live
audit (2026-09-24, planning/EXECUTION_LOG.md):

1. A FAILED registry row used to permanently block all future retries with
   409 CONFLICT forever -- no retry path existed despite a code comment
   claiming one did. Fixed via TestCaseRegistryRepository.retry_after_failure().
2. Idempotent retry of a successfully-created TestCase always reported
   status="EXECUTABLE", even though no STAGED->EXECUTABLE transition code
   exists anywhere -- confirmed_at was conflated with OM's own executable
   state. Fixed to always report "STAGED" on the idempotent-return path.

Both bugs were live-verified against a real running Backend + OpenMetadata
instance before being fixed here; these tests are the permanent regression
coverage the manifest's "logical lifecycle, validate-before-write, compliance
rollup" required-tests column calls for (previously entirely unmet -- zero
test files existed for dq_service.py before this file).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.core.config import Settings
from app.core.errors import ConflictError, ValidationError
from app.services.dq_service import (
    DQService,
    build_natural_key_hash,
    build_stable_test_slot_id,
)


def settings() -> Settings:
    return Settings(app_env="test")


def om_client(*, create_result: dict | None = None, create_side_effect=None) -> MagicMock:
    client = MagicMock()
    client.build_entity_link.return_value = "<#E::table::financial.crm.customers>"
    if create_side_effect is not None:
        client.create_test_case.side_effect = create_side_effect
    else:
        client.create_test_case.return_value = create_result or {"id": "om-tc-1"}
    return client


def service(session, *, om: MagicMock) -> DQService:
    return DQService(session, settings(), om_client=om)


def test_build_stable_test_slot_id_uses_test_key_when_present() -> None:
    assert build_stable_test_slot_id(rule_id="r1", test_key="slot-a") == "r1::slot-a"
    assert build_stable_test_slot_id(rule_id="r1", test_key=None) == "r1"


def test_build_natural_key_hash_is_deterministic_and_prefixed() -> None:
    h1 = build_natural_key_hash(
        target_entity_fqn="a.b.c", test_definition_fqn="d", stable_test_slot_id="r1"
    )
    h2 = build_natural_key_hash(
        target_entity_fqn="a.b.c", test_definition_fqn="d", stable_test_slot_id="r1"
    )
    assert h1 == h2
    assert h1.startswith("dg_")
    assert len(h1) == len("dg_") + 32


def test_missing_required_fields_raises_validation_error(session) -> None:
    svc = service(session, om=om_client())
    with pytest.raises(ValidationError):
        svc.create_staged_test_case(
            target_asset_fqn="",
            test_definition_fqn="d",
            parameter_values={},
            rule_id="r1",
            test_key=None,
            column_name=None,
            worker_id="w1",
        )
    svc.om_client.create_test_case.assert_not_called()


def test_fresh_create_returns_staged_and_confirms_registry(session) -> None:
    om = om_client(create_result={"id": "om-tc-1"})
    svc = service(session, om=om)

    result = svc.create_staged_test_case(
        target_asset_fqn="financial.crm.customers",
        test_definition_fqn="columnValuesToBeNotNull",
        parameter_values={},
        rule_id="rule-1",
        test_key=None,
        column_name="email",
        worker_id="worker-1",
    )

    assert result["status"] == "STAGED"
    assert result["om_testcase_id"] == "om-tc-1"
    om.create_test_case.assert_called_once()

    record = svc.registry.get_by_natural_key_hash(result["natural_key_hash"])
    assert record.reservation_state == "CONFIRMED"
    assert record.om_testcase_id == "om-tc-1"


def test_second_call_by_different_worker_is_reserved_conflict(session) -> None:
    """A RESERVED row (another worker's in-flight attempt, never confirmed or
    failed) must still 409 -- only FAILED gets the new retry path."""
    om = om_client()
    svc = service(session, om=om)

    # Manually reserve without confirming, simulating an in-flight worker.
    svc.registry.reserve(
        natural_key_hash=build_natural_key_hash(
            target_entity_fqn="financial.crm.customers",
            test_definition_fqn="columnValuesToBeNotNull",
            stable_test_slot_id="rule-1",
        ),
        target_entity_fqn="financial.crm.customers",
        test_definition_fqn="columnValuesToBeNotNull",
        stable_test_slot_id="rule-1",
        worker_id="worker-in-flight",
    )
    session.commit()

    with pytest.raises(ConflictError):
        svc.create_staged_test_case(
            target_asset_fqn="financial.crm.customers",
            test_definition_fqn="columnValuesToBeNotNull",
            parameter_values={},
            rule_id="rule-1",
            test_key=None,
            column_name=None,
            worker_id="worker-2",
        )
    om.create_test_case.assert_not_called()


def test_idempotent_retry_of_confirmed_row_always_reports_staged(session) -> None:
    """Regression for bug 2: a successfully-created TestCase must always
    report STAGED on retry, never EXECUTABLE (no EXECUTABLE-transition code
    exists anywhere in this codebase yet)."""
    om = om_client(create_result={"id": "om-tc-1"})
    svc = service(session, om=om)

    first = svc.create_staged_test_case(
        target_asset_fqn="financial.crm.customers",
        test_definition_fqn="columnValuesToBeNotNull",
        parameter_values={},
        rule_id="rule-1",
        test_key=None,
        column_name=None,
        worker_id="worker-1",
    )
    assert first["status"] == "STAGED"

    second = svc.create_staged_test_case(
        target_asset_fqn="financial.crm.customers",
        test_definition_fqn="columnValuesToBeNotNull",
        parameter_values={},
        rule_id="rule-1",
        test_key=None,
        column_name=None,
        worker_id="worker-2",
    )
    assert second["status"] == "STAGED"
    assert second["id"] == first["id"]
    assert second["om_testcase_id"] == first["om_testcase_id"]
    # Only one OM create call across both attempts -- the second was a pure
    # idempotent registry read, never touched OM.
    om.create_test_case.assert_called_once()


def test_failed_write_is_marked_failed_and_does_not_confirm(session) -> None:
    om = om_client(create_side_effect=RuntimeError("OM 401"))
    svc = service(session, om=om)

    with pytest.raises(RuntimeError):
        svc.create_staged_test_case(
            target_asset_fqn="financial.crm.customers",
            test_definition_fqn="columnValuesToBeNotNull",
            parameter_values={},
            rule_id="rule-1",
            test_key=None,
            column_name=None,
            worker_id="worker-1",
        )

    natural_key_hash = build_natural_key_hash(
        target_entity_fqn="financial.crm.customers",
        test_definition_fqn="columnValuesToBeNotNull",
        stable_test_slot_id="rule-1",
    )
    record = svc.registry.get_by_natural_key_hash(natural_key_hash)
    assert record.reservation_state == "FAILED"
    assert record.om_testcase_id is None


def test_retry_after_failure_succeeds_instead_of_409_forever(session) -> None:
    """Regression for bug 1: a FAILED row must be retryable, not a permanent
    409 CONFLICT. First attempt fails (simulated transient OM error), second
    attempt with the same logical request must succeed."""
    failing_om = om_client(create_side_effect=RuntimeError("transient OM 401"))
    svc = service(session, om=failing_om)

    with pytest.raises(RuntimeError):
        svc.create_staged_test_case(
            target_asset_fqn="financial.crm.customers",
            test_definition_fqn="columnValuesToBeNotNull",
            parameter_values={},
            rule_id="rule-1",
            test_key=None,
            column_name=None,
            worker_id="worker-1",
        )

    # Second attempt: OM now succeeds (simulates the transient error clearing).
    svc.om_client = om_client(create_result={"id": "om-tc-recovered"})
    result = svc.create_staged_test_case(
        target_asset_fqn="financial.crm.customers",
        test_definition_fqn="columnValuesToBeNotNull",
        parameter_values={},
        rule_id="rule-1",
        test_key=None,
        column_name=None,
        worker_id="worker-2",
    )

    assert result["status"] == "STAGED"
    assert result["om_testcase_id"] == "om-tc-recovered"

    record = svc.registry.get_by_natural_key_hash(result["natural_key_hash"])
    assert record.reservation_state == "CONFIRMED"
    assert record.worker_id == "worker-2"


def test_retry_after_failure_twice_in_a_row_keeps_working(session) -> None:
    """A row can fail, retry-fail again, and still eventually succeed --
    retry_after_failure must not be a one-shot escape hatch."""
    svc = service(session, om=om_client(create_side_effect=RuntimeError("fail 1")))

    with pytest.raises(RuntimeError):
        svc.create_staged_test_case(
            target_asset_fqn="a.b.c",
            test_definition_fqn="d",
            parameter_values={},
            rule_id="r1",
            test_key=None,
            column_name=None,
            worker_id="w1",
        )

    svc.om_client = om_client(create_side_effect=RuntimeError("fail 2"))
    with pytest.raises(RuntimeError):
        svc.create_staged_test_case(
            target_asset_fqn="a.b.c",
            test_definition_fqn="d",
            parameter_values={},
            rule_id="r1",
            test_key=None,
            column_name=None,
            worker_id="w2",
        )

    svc.om_client = om_client(create_result={"id": "om-final"})
    result = svc.create_staged_test_case(
        target_asset_fqn="a.b.c",
        test_definition_fqn="d",
        parameter_values={},
        rule_id="r1",
        test_key=None,
        column_name=None,
        worker_id="w3",
    )
    assert result["status"] == "STAGED"
    assert result["om_testcase_id"] == "om-final"
