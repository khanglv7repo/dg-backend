from __future__ import annotations

from app.core.config import Settings
from app.models.enums import JobType
from app.services.classification_commands import ClassificationCommandService


def test_manual_classification_command_enqueues_om_hydration_job(session) -> None:
    settings = Settings(_env_file=None, openmetadata_enabled=True)
    with session.begin():
        job = ClassificationCommandService(session, settings).enqueue_asset(
            entity_type="table",
            entity_fqn="postgres.sales.customers",
            correlation_id="corr",
        )

    assert job.job_type == JobType.CLASSIFY_ASSET_FROM_OM.value
    assert job.payload["entity_fqn"] == "postgres.sales.customers"
    assert job.payload["event_id"].startswith("manual-classification:")
