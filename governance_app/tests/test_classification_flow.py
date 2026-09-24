from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.core.config import Settings
from app.models.enums import (
    ClassificationAction,
    JobType,
)
from app.repositories.jobs import JobRepository
from app.schemas.events import (
    MetadataEventRequest,
    MetadataField,
)
from app.services.classification import (
    ClassificationService,
)


def event(
    column_name: str,
    event_id: str,
) -> MetadataEventRequest:
    return MetadataEventRequest(
        event_id=event_id,
        event_type="ENTITY_CREATED",
        entity_type="table",
        entity_fqn="hive.sales.customers",
        entity_name="customers",
        fields=[
            MetadataField(
                name=column_name,
                data_type="varchar",
            )
        ],
    )


def test_trusted_exact_rule_enqueues_direct_confirmed_tag_application(
    session,
    active_classification_rules,
) -> None:
    """After the GovernanceJob→Celery migration, ClassificationService dispatches
    APPLY_CONFIRMED_TAGS via apply_confirmed_tags.delay() instead of
    JobRepository.enqueue(). Verify the Celery task fires for a trusted exact match.
    """
    settings = Settings(trusted_auto_apply_enabled=True)

    fake_async = MagicMock()
    fake_async.id = "00000000-0000-0000-0000-000000000011"

    with patch(
        "app.tasks.classification.apply_confirmed_tags.delay",
        return_value=fake_async,
    ) as mock_delay:
        with session.begin():
            result = ClassificationService(session, settings).classify(event("email", "evt-1"))

    assert result["action"] == ClassificationAction.AUTO_APPLY.value
    mock_delay.assert_called_once()


def test_non_trusted_rule_enqueues_openmetadata_suggestion(
    session,
    active_classification_rules,
) -> None:
    """After the GovernanceJob→Celery migration, ClassificationService dispatches
    CREATE_OM_SUGGESTIONS via create_om_suggestions.delay() instead of
    JobRepository.enqueue(). Verify the Celery task fires for a non-trusted match.
    """
    settings = Settings(trusted_auto_apply_enabled=True)

    fake_async = MagicMock()
    fake_async.id = "00000000-0000-0000-0000-000000000012"

    with patch(
        "app.tasks.classification.create_om_suggestions.delay",
        return_value=fake_async,
    ) as mock_delay:
        with session.begin():
            result = ClassificationService(session, settings).classify(
                event("work_email_address", "evt-2")
            )

    assert result["action"] == ClassificationAction.OPENMETADATA_SUGGESTION.value
    mock_delay.assert_called_once()


def test_no_match_uses_agent_job_when_agent_worker_enabled(
    session,
    active_classification_rules,
) -> None:
    settings = Settings(
        agent_enabled=True,
        trusted_auto_apply_enabled=False,
    )

    with session.begin():
        result = ClassificationService(
            session,
            settings,
        ).classify(
            event(
                "unrecognized_business_field",
                "evt-agent-fallback",
            )
        )

    claimed = JobRepository(
        session
    ).claim_batch(
        worker_id="agent-worker",
        limit=10,
        allowed_job_types={
            JobType.AGENT_CLASSIFY
        },
    )

    assert (
        result["action"]
        == ClassificationAction.AGENT_FALLBACK.value
    )
    assert len(claimed) == 1
    assert (
        claimed[0].job_type
        == JobType.AGENT_CLASSIFY.value
    )
