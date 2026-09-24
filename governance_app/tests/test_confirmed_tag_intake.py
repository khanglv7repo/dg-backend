from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.core.config import Settings
from app.schemas.events import ConfirmedTagEventRequest
from app.services.intake import IntakeService


def test_confirmed_tag_intake_queues_live_openmetadata_refresh(session) -> None:
    """After the GovernanceJob→Celery migration, IntakeService dispatches
    accept_confirmed_tag_event via sync_tags_to_ranger.delay() instead of
    JobRepository.enqueue(SYNC_RANGER_TAGS).
    Verify the Celery task is called with the right args (caller-supplied tags
    are NOT forwarded — worker reads live OM state instead).
    """
    settings = Settings(_env_file=None)
    request = ConfirmedTagEventRequest(
        event_id="evt-confirmed",
        source="SUGGESTION_ACCEPTED",
        entity_type="table",
        entity_fqn="hive.sales.customers",
        tags=["STALE.Tag.From.Caller"],
        field_paths={"STALE.Tag.From.Caller": ["columns.email"]},
        correlation_id="corr",
    )

    fake_async = MagicMock()
    fake_async.id = "00000000-0000-0000-0000-000000000002"

    with patch(
        "app.tasks.tag_sync.sync_tags_to_ranger.delay",
        return_value=fake_async,
    ) as mock_delay:
        with session.begin():
            job = IntakeService(session, settings).accept_confirmed_tag_event(request)

    # After migration: returns _DispatchedTaskRef wrapping the Celery task
    assert str(job.id) == "00000000-0000-0000-0000-000000000002"

    # Verify Celery task called with entity identity, NOT with caller-supplied tags
    call_kwargs = mock_delay.call_args.kwargs
    assert call_kwargs["entity_type"] == "table"
    assert call_kwargs["entity_fqn"] == "hive.sales.customers"
    assert call_kwargs["correlation_id"] == "corr"
    # Caller-supplied tags must NOT be forwarded (worker reads live OM state)
    assert "tags" not in call_kwargs
    assert "field_paths" not in call_kwargs
