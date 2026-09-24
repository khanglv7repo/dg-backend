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


def test_trino_verification_disabled_marks_synchronized_projection_unavailable() -> None:
    db = MagicMock()
    db.scalars.return_value = []
    session_cm = MagicMock()
    session_cm.__enter__.return_value = db
    session_cm.__exit__.return_value = False

    with patch.object(policy_task, "SessionLocal", return_value=session_cm), patch.object(
        policy_task,
        "get_settings",
        return_value=MagicMock(
            trino_readonly_enabled=False,
            trino_readonly_user=None,
        ),
    ):
        result = policy_task.verify_trino_policy_enforcement.run()

    assert result["status"] == "NO_PROJECTIONS"
    assert result["drift"] == 0
    assert result["unavailable"] == 0


def test_trino_verification_disabled_never_reports_drift_for_existing_projection() -> None:
    projection = SimpleNamespace(
        verification_status="UNVERIFIED",
        verification_details={},
        last_verified_at=None,
    )
    db = MagicMock()
    db.scalars.return_value = [projection]
    session_cm = MagicMock()
    session_cm.__enter__.return_value = db
    session_cm.__exit__.return_value = False

    with patch.object(policy_task, "SessionLocal", return_value=session_cm), patch.object(
        policy_task,
        "get_settings",
        return_value=MagicMock(
            trino_readonly_enabled=False,
            trino_readonly_user=None,
        ),
    ):
        result = policy_task.verify_trino_policy_enforcement.run()

    assert result["status"] == "VERIFICATION_UNAVAILABLE"
    assert result["drift"] == 0
    assert result["unavailable"] == 1
    assert projection.verification_status == "VERIFICATION_UNAVAILABLE"
