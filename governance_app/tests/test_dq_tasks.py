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
    repository.run_recovery_candidates.return_value = []

    with patch.object(dq_task, "SessionLocal", return_value=_session_cm(session)), patch.object(
        dq_task, "TestCaseRegistryRepository", return_value=repository
    ), patch.object(
        dq_task.materialize_approved_test_case, "delay"
    ) as delay, patch.object(
        dq_task, "get_settings",
        return_value=SimpleNamespace(
            dq_registry_reservation_ttl_seconds=120,
            dq_runner_timeout_seconds=600,
        ),
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
    repository.run_recovery_candidates.return_value = []
    om = MagicMock()
    om.find_test_case_by_entity_and_name.return_value = {
        "id": "om-legacy",
        "fullyQualifiedName": "dev.sales.customer.dg_legacy",
        "testSuite": {
            "id": "suite-1",
            "fullyQualifiedName": "dev.sales.customer.testSuite",
        },
    }
    om.get_test_suite_by_name.return_value = {
        "id": "suite-1",
        "fullyQualifiedName": "dev.sales.customer.testSuite",
        "basic": True,
        "basicEntityReference": {
            "type": "table",
            "fullyQualifiedName": "dev.sales.customer",
        },
    }

    settings = SimpleNamespace(
        dq_registry_reservation_ttl_seconds=120,
        dq_runner_timeout_seconds=600,
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
        om_test_suite_fqn="dev.sales.customer.testSuite",
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


def test_run_task_reads_om_result_and_treats_assertion_failure_as_completed() -> None:
    session = MagicMock()
    om = MagicMock()
    runner = MagicMock()
    service = MagicMock()
    service.mark_run_started.return_value = {
        "run_status": "RUNNING",
    }
    service.latest_result_for_active_run.side_effect = [
        None,
        {
            "timestamp": 1234567890000,
            "testCaseStatus": "Failed",
            "testResultValue": [],
        },
    ]
    service.get.return_value = {
        "target_entity_fqn": "dev.sales.customer",
        "om_test_suite_fqn": "dev.sales.customer.testSuite",
        "natural_key_hash": "dg_case",
    }
    service.complete_run.return_value = {
        "id": "r1",
        "run_id": "run-1",
        "run_status": "COMPLETED",
        "last_result": {
            "testCaseStatus": "Failed",
        },
    }

    settings = SimpleNamespace(
        dq_runner_url="http://metadata-ingestion:8080",
        dq_runner_timeout_seconds=600,
    )

    with patch.object(dq_task, "SessionLocal", return_value=_session_cm(session)), patch.object(
        dq_task, "_execution_om_client", return_value=om
    ), patch.object(dq_task, "DQService", return_value=service), patch.object(
        dq_task, "DQRunnerClient", return_value=runner
    ), patch.object(
        dq_task, "get_settings", return_value=settings
    ):
        result = dq_task.run_executable_test_case.run(
            registry_id="r1",
            run_id="run-1",
        )

    runner.run_test_case.assert_called_once_with(
        table_fqn="dev.sales.customer",
        test_suite_fqn="dev.sales.customer.testSuite",
        test_case_name="dg_case",
    )
    service.complete_run.assert_called_once()
    assert result["run_status"] == "COMPLETED"
    assert result["last_result"]["testCaseStatus"] == "Failed"
    runner.close.assert_called_once()
    om.close.assert_called_once()


def test_recovery_redispatches_stale_run_with_same_run_id() -> None:
    session = MagicMock()
    repository = MagicMock()
    repository.approved_materialization_candidates.return_value = []
    repository.crash_recovery_candidates.return_value = []
    repository.run_recovery_candidates.return_value = [
        SimpleNamespace(id="r1", active_run_id="run-1")
    ]

    settings = SimpleNamespace(
        dq_registry_reservation_ttl_seconds=120,
        dq_runner_timeout_seconds=600,
    )

    with patch.object(dq_task, "SessionLocal", return_value=_session_cm(session)), patch.object(
        dq_task, "TestCaseRegistryRepository", return_value=repository
    ), patch.object(
        dq_task.run_executable_test_case, "delay"
    ) as delay, patch.object(
        dq_task, "get_settings", return_value=settings
    ):
        result = dq_task.recover_testcase_registry.run()

    delay.assert_called_once_with(registry_id="r1", run_id="run-1")
    assert result["run_redispatched"] == 1


def test_legacy_recovery_never_marks_logical_suite_executable() -> None:
    session = MagicMock()
    repository = MagicMock()
    repository.approved_materialization_candidates.return_value = []
    legacy = SimpleNamespace(
        id="legacy-logical",
        natural_key_hash="dg_legacy_logical",
        target_entity_fqn="dev.sales.customer",
        lifecycle_state="STAGED",
        spec_payload={},
    )
    repository.crash_recovery_candidates.return_value = [legacy]
    repository.run_recovery_candidates.return_value = []

    om = MagicMock()
    om.find_test_case_by_entity_and_name.return_value = {
        "id": "om-legacy-logical",
        "fullyQualifiedName": "dev.sales.customer.dg_legacy_logical",
        "testSuite": {
            "id": "suite-logical",
            "fullyQualifiedName": "logical.quality.suite",
        },
    }
    om.get_test_suite_by_name.return_value = {
        "id": "suite-logical",
        "fullyQualifiedName": "logical.quality.suite",
        "basic": False,
        "executable": False,
    }

    settings = SimpleNamespace(
        dq_registry_reservation_ttl_seconds=120,
        dq_runner_timeout_seconds=600,
        openmetadata_execution_bot_token=None,
        openmetadata_base_url="http://om/api",
        openmetadata_timeout_seconds=10,
    )

    with patch.object(
        dq_task, "SessionLocal", return_value=_session_cm(session)
    ), patch.object(
        dq_task, "TestCaseRegistryRepository", return_value=repository
    ), patch.object(
        dq_task, "_execution_om_client", return_value=om
    ), patch.object(
        dq_task, "get_settings", return_value=settings
    ):
        result = dq_task.recover_testcase_registry.run()

    repository.mark_executable.assert_not_called()
    repository.mark_failed.assert_called_once_with("legacy-logical")
    assert result["reconciled"] == 0
    assert result["still_missing"] == 1
