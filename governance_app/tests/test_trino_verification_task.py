from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.models.job import utcnow
from app.tasks import policy_sync as policy_task


def _session_cm(session):
    cm = MagicMock()
    cm.__enter__.return_value = session
    cm.__exit__.return_value = False
    return cm


def _projection():
    return SimpleNamespace(
        id="projection-1",
        projection_type="ACCESS",
        projection_key="access",
        ranger_policy_name="dg-policy-access",
        sync_status="SYNCHRONIZED",
        last_reconciled_at=utcnow() - timedelta(seconds=60),
        verification_status="UNVERIFIED",
        verification_details={},
        last_verified_at=None,
    )


def _version():
    return SimpleNamespace(
        id="version-1",
        policy_key="sales.customer",
        version=1,
        status="ACTIVE",
        logical_policy={
            "subjects": [{"type": "USER", "name": "alice"}],
            "resource": {
                "catalog": "dev",
                "schema": "sales",
                "table": "customer",
            },
            "access": {"select": "ALLOW"},
            "masks": {},
            "row_filter": None,
        },
    )


def _settings():
    return SimpleNamespace(
        trino_readonly_enabled=True,
        trino_readonly_user="alice",
        eventual_consistency_window_seconds=50,
    )


def test_verification_task_records_runtime_drift_without_changing_sync_status() -> None:
    projection = _projection()
    version = _version()
    db = MagicMock()
    db.execute.return_value.all.return_value = [(projection, version)]

    verifier = MagicMock()
    verifier.verify.return_value = {
        "status": "RUNTIME_DRIFT",
        "observed": "ACCESS_DENIED",
        "expected": "QUERY_SUCCESS",
    }

    with patch.object(
        policy_task, "SessionLocal", return_value=_session_cm(db)
    ), patch.object(
        policy_task, "get_settings", return_value=_settings()
    ), patch.object(
        policy_task,
        "PolicyRuntimeVerificationService",
        return_value=verifier,
    ):
        result = policy_task.verify_trino_policy_enforcement.run()

    assert result["status"] == "RUNTIME_DRIFT"
    assert result["drift"] == 1
    assert projection.sync_status == "SYNCHRONIZED"
    assert projection.verification_status == "RUNTIME_DRIFT"
    assert projection.verification_details["observed"] == "ACCESS_DENIED"
    db.commit.assert_called_once()


def test_confirmed_verification_persists_runtime_evidence() -> None:
    projection = _projection()
    version = _version()
    db = MagicMock()
    db.execute.return_value.all.return_value = [(projection, version)]

    verifier = MagicMock()
    verifier.verify.return_value = {
        "status": "VERIFICATION_CONFIRMED",
        "observed": "QUERY_SUCCESS",
        "expected": "QUERY_SUCCESS",
        "query_id": "q-1",
    }

    with patch.object(
        policy_task, "SessionLocal", return_value=_session_cm(db)
    ), patch.object(
        policy_task, "get_settings", return_value=_settings()
    ), patch.object(
        policy_task,
        "PolicyRuntimeVerificationService",
        return_value=verifier,
    ):
        result = policy_task.verify_trino_policy_enforcement.run()

    assert result["status"] == "VERIFICATION_CONFIRMED"
    assert result["confirmed"] == 1
    assert projection.sync_status == "SYNCHRONIZED"
    assert projection.verification_status == "VERIFICATION_CONFIRMED"
    assert projection.verification_details["query_id"] == "q-1"
    db.commit.assert_called_once()
