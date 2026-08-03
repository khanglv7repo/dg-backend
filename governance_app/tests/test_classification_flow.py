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
    settings = Settings(
        trusted_auto_apply_enabled=True
    )

    with session.begin():
        result = ClassificationService(
            session,
            settings,
        ).classify(
            event("email", "evt-1")
        )

    claimed = JobRepository(
        session
    ).claim_batch(
        worker_id="test",
        limit=10,
    )

    assert (
        result["action"]
        == ClassificationAction.AUTO_APPLY.value
    )
    assert len(claimed) == 1
    assert (
        claimed[0].job_type
        == JobType.APPLY_CONFIRMED_TAGS.value
    )


def test_non_trusted_rule_enqueues_openmetadata_suggestion(
    session,
    active_classification_rules,
) -> None:
    settings = Settings(
        trusted_auto_apply_enabled=True
    )

    with session.begin():
        result = ClassificationService(
            session,
            settings,
        ).classify(
            event(
                "work_email_address",
                "evt-2",
            )
        )

    claimed = JobRepository(
        session
    ).claim_batch(
        worker_id="test",
        limit=10,
    )

    assert (
        result["action"]
        == ClassificationAction.OPENMETADATA_SUGGESTION.value
    )
    assert len(claimed) == 1
    assert (
        claimed[0].job_type
        == JobType.CREATE_OM_SUGGESTIONS.value
    )


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
