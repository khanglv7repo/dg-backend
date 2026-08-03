from __future__ import annotations

from app.core.config import Settings
from app.models.enums import JobType
from app.schemas.events import ConfirmedTagEventRequest
from app.services.intake import IntakeService


def test_confirmed_tag_intake_queues_live_openmetadata_refresh(session) -> None:
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

    with session.begin():
        job = IntakeService(session, settings).accept_confirmed_tag_event(request)

    assert job.job_type == JobType.SYNC_RANGER_TAGS.value
    assert job.payload == {
        "entity_type": "table",
        "entity_fqn": "hive.sales.customers",
        "classification_run_id": None,
        "correlation_id": "corr",
    }
    assert "tags" not in job.payload
    assert "field_paths" not in job.payload
