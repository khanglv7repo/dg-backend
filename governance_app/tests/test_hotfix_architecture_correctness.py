from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.tasks import outbox as outbox_task
from app.tasks import policy_sync as policy_task


def test_outbox_policy_event_dispatches_real_reconciliation_task() -> None:
    record = SimpleNamespace(
        id="event-1",
        aggregate_type="data_access_policy",
        aggregate_id="sales.customer",
        event_type="policy.version.activated",
        payload={
            "policy_version_id": "version-1",
            "correlation_id": "corr-1",
        },
    )

    with patch.object(outbox_task.sync_policy_to_ranger, "delay") as delay:
        delay.return_value.id = "task-1"
        outbox_task._publish(record)

    delay.assert_called_once_with(
        policy_version_id="version-1",
        correlation_id="corr-1",
    )


def test_outbox_unknown_event_fails_instead_of_being_marked_delivered() -> None:
    record = SimpleNamespace(
        id="event-2",
        aggregate_type="data_access_policy",
        aggregate_id="sales.customer",
        event_type="unknown.event",
        payload={"policy_version_id": "version-1"},
    )

    with pytest.raises(ValueError, match="unsupported outbox event_type"):
        outbox_task._publish(record)


def test_trino_verification_without_real_plan_never_reports_runtime_drift() -> None:
    db = MagicMock()
    db.execute.return_value.scalar_one.return_value = 3
    session_cm = MagicMock()
    session_cm.__enter__.return_value = db
    session_cm.__exit__.return_value = False

    with patch.object(policy_task, "SessionLocal", return_value=session_cm):
        result = policy_task.verify_trino_policy_enforcement.run()

    assert result == {
        "status": "VERIFICATION_UNAVAILABLE",
        "confirmed": 0,
        "pending": 0,
        "drift": 0,
        "unavailable": 3,
    }


def test_trino_verification_no_projection_is_not_drift() -> None:
    db = MagicMock()
    db.execute.return_value.scalar_one.return_value = 0
    session_cm = MagicMock()
    session_cm.__enter__.return_value = db
    session_cm.__exit__.return_value = False

    with patch.object(policy_task, "SessionLocal", return_value=session_cm):
        result = policy_task.verify_trino_policy_enforcement.run()

    assert result["status"] == "NO_PROJECTIONS"
    assert result["drift"] == 0
    assert result["unavailable"] == 0
