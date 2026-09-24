from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.core.errors import ExternalSystemError, ValidationError
from app.tasks import dq as dq_task


def _session_cm(session):
    cm = MagicMock()
    cm.__enter__.return_value = session
    cm.__exit__.return_value = False
    return cm


def test_materialize_task_uses_execution_bot_and_service() -> None:
    session = MagicMock()
    om = MagicMock()
    service = MagicMock()
    service.materialize_approved_test_case.return_value = {
        "id": "r1",
        "status": "EXECUTABLE",
    }

    with patch.object(dq_task, "SessionLocal", return_value=_session_cm(session)), patch.object(
        dq_task, "_execution_om_client", return_value=om
    ), patch.object(dq_task, "DQService", return_value=service):
        result = dq_task.materialize_approved_test_case.run(registry_id="r1")

    assert result["status"] == "EXECUTABLE"
    service.materialize_approved_test_case.assert_called_once_with(registry_id="r1")
    om.close.assert_called_once()


def test_recovery_redispatches_approved_candidate_once() -> None:
    session = MagicMock()
    repository = MagicMock()
    repository.approved_materialization_candidates.return_value = [
        SimpleNamespace(id="approved-1")
    ]
    repository.crash_recovery_candidates.return_value = []

    with patch.object(dq_task, "SessionLocal", return_value=_session_cm(session)), patch.object(
        dq_task, "TestCaseRegistryRepository", return_value=repository
    ), patch.object(
        dq_task.materialize_approved_test_case, "delay"
    ) as delay, patch.object(
        dq_task, "get_settings",
        return_value=SimpleNamespace(dq_registry_reservation_ttl_seconds=120),
    ):
        result = dq_task.recover_testcase_registry.run()

    delay.assert_called_once_with(registry_id="approved-1")
    assert result["redispatched"] == 1
    assert result["reconciled"] == 0


def test_legacy_staged_reserved_row_reconciles_existing_om_testcase() -> None:
    session = MagicMock()
    repository = MagicMock()
    repository.approved_materialization_candidates.return_value = []
    legacy = SimpleNamespace(
        id="legacy-1",
        natural_key_hash="dg_legacy",
        target_entity_fqn="dev.sales.customer",
        lifecycle_state="STAGED",
        spec_payload={},
    )
    repository.crash_recovery_candidates.return_value = [legacy]
    om = MagicMock()
    om.find_test_case_by_entity_and_name.return_value = {
        "id": "om-legacy",
        "fullyQualifiedName": "dev.sales.customer.dg_legacy",
        "testSuite": {"id": "suite-1"},
    }

    settings = SimpleNamespace(
        dq_registry_reservation_ttl_seconds=120,
        openmetadata_execution_bot_token=None,
        openmetadata_base_url="http://om/api",
        openmetadata_timeout_seconds=10,
    )

    with patch.object(dq_task, "SessionLocal", return_value=_session_cm(session)), patch.object(
        dq_task, "TestCaseRegistryRepository", return_value=repository
    ), patch.object(dq_task, "_execution_om_client", return_value=om), patch.object(
        dq_task, "get_settings", return_value=settings
    ):
        result = dq_task.recover_testcase_registry.run()

    repository.mark_executable.assert_called_once_with(
        "legacy-1",
        om_testcase_id="om-legacy",
        om_testcase_fqn="dev.sales.customer.dg_legacy",
    )
    assert result["reconciled"] == 1
    assert result["redispatched"] == 0


def test_materialize_task_permanent_validation_failure_marks_failed() -> None:
    session = MagicMock()
    repository = MagicMock()
    om = MagicMock()
    service = MagicMock()
    service.materialize_approved_test_case.side_effect = ValidationError("bad staged spec")

    with patch.object(dq_task, "SessionLocal", return_value=_session_cm(session)), patch.object(
        dq_task, "_execution_om_client", return_value=om
    ), patch.object(dq_task, "DQService", return_value=service), patch.object(
        dq_task, "TestCaseRegistryRepository", return_value=repository
    ):
        result = dq_task.materialize_approved_test_case.run(registry_id="r1")

    assert result["status"] == "FAILED"
    assert result["retryable"] is False
    repository.mark_materialization_failed.assert_called_once_with(
        "r1", permanent=True
    )


def test_materialize_task_nonretryable_om_failure_marks_failed() -> None:
    session = MagicMock()
    repository = MagicMock()
    om = MagicMock()
    service = MagicMock()
    service.materialize_approved_test_case.side_effect = ExternalSystemError(
        "forbidden",
        system="openmetadata",
        retryable=False,
    )

    with patch.object(dq_task, "SessionLocal", return_value=_session_cm(session)), patch.object(
        dq_task, "_execution_om_client", return_value=om
    ), patch.object(dq_task, "DQService", return_value=service), patch.object(
        dq_task, "TestCaseRegistryRepository", return_value=repository
    ):
        result = dq_task.materialize_approved_test_case.run(registry_id="r1")

    assert result["status"] == "FAILED"
    repository.mark_materialization_failed.assert_called_once_with(
        "r1", permanent=True
    )
