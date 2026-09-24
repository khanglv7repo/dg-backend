from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.core.config import Settings
from app.services.classification_commands import ClassificationCommandService


def test_manual_classification_command_enqueues_om_hydration_job(session) -> None:
    """After the GovernanceJob→Celery migration, ClassificationCommandService dispatches
    via classify_asset_from_openmetadata.delay() instead of JobRepository.enqueue().
    Verify the Celery task is called with the right payload.
    """
    settings = Settings(_env_file=None, openmetadata_enabled=True)

    fake_async = MagicMock()
    fake_async.id = "00000000-0000-0000-0000-000000000001"

    with patch(
        "app.tasks.classification.classify_asset_from_openmetadata.delay",
        return_value=fake_async,
    ) as mock_delay:
        with session.begin():
            job = ClassificationCommandService(session, settings).enqueue_asset(
                entity_type="table",
                entity_fqn="postgres.sales.customers",
                correlation_id="corr",
            )

    # After migration: returns _DispatchedTaskRef wrapping the Celery task
    assert str(job.id) == "00000000-0000-0000-0000-000000000001"
    assert job.status  # has a status attribute

    # Verify payload passed to Celery task
    call_kwargs = mock_delay.call_args.kwargs
    payload = call_kwargs["payload"]
    assert payload["entity_fqn"] == "postgres.sales.customers"
    assert payload["event_id"].startswith("manual-classification:")
