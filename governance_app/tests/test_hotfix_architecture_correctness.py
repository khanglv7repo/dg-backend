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


def _session_cm(db):
    cm = MagicMock()
    cm.__enter__.return_value = db
    cm.__exit__.return_value = False
    return cm


def test_trino_verification_disabled_with_no_projection_returns_no_projections() -> None:
    db = MagicMock()
    db.execute.return_value.all.return_value = []

    with patch.object(
        policy_task, "SessionLocal", return_value=_session_cm(db)
    ), patch.object(
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


def test_trino_verification_disabled_marks_existing_projection_unavailable() -> None:
    projection = SimpleNamespace(
        verification_status="UNVERIFIED",
        verification_details={},
        last_verified_at=None,
    )
    version = SimpleNamespace()
    db = MagicMock()
    db.execute.return_value.all.return_value = [(projection, version)]

    with patch.object(
        policy_task, "SessionLocal", return_value=_session_cm(db)
    ), patch.object(
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
    db.commit.assert_called_once()


def test_backend_openmetadata_adapter_has_no_tag_write_capability() -> None:
    from app.clients.openmetadata import OpenMetadataClient

    forbidden = (
        "apply_confirmed_tags",
        "create_tag_suggestion",
        "_merge_entity_tags",
        "_merge_column_tags",
        "_merge_confirmed_tag_labels",
    )
    for method_name in forbidden:
        assert not hasattr(OpenMetadataClient, method_name), method_name


def test_legacy_jobs_and_classification_routes_are_not_exposed() -> None:
    from app.api.router import api_router

    paths = {route.path for route in api_router.routes}
    assert not any(path.startswith("/jobs") for path in paths)
    assert not any(path.startswith("/classification-runs") for path in paths)
