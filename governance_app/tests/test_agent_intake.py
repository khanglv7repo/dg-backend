import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.core.errors import ConfigurationError
from app.models.audit import AuditEvent
from app.models.enums import (
    ClassificationAction,
    JobType,
)
from app.repositories.jobs import JobRepository
from app.schemas.classification import TagSuggestion
from app.schemas.events import (
    AgentClassificationEventRequest,
)
from app.services.classification import (
    AgentClassificationResultService,
)


def request(
    tag: str = "PII.Email",
) -> AgentClassificationEventRequest:
    return AgentClassificationEventRequest(
        event_id="agent-evt-1",
        entity_type="table",
        entity_fqn="hive.sales.customers",
        agent_name="classification-agent",
        graph_version="graph-v1",
        model="test-model",
        prompt_version="prompt-v1",
        input_fingerprint="12345678abcdef",
        suggestions=[
            TagSuggestion(
                tag=tag,
                confidence=0.88,
                rationale=(
                    "MCP context indicates "
                    "an email field"
                ),
                field_path="columns.email",
            )
        ],
    )


def test_agent_result_only_enqueues_openmetadata_suggestion(
    session,
    active_classification_rules,
) -> None:
    settings = Settings(
        agent_enabled=True
    )
    with session.begin():
        result = (
            AgentClassificationResultService(
                session,
                settings,
            ).accept(request())
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
    assert (
        claimed[0].payload["source_kind"]
        == "AGENT"
    )


def test_agent_result_rejects_tags_outside_allowlist(
    session,
    active_classification_rules,
) -> None:
    settings = Settings(
        agent_enabled=True
    )

    with pytest.raises(
        ConfigurationError,
        match="outside the governed allow-list",
    ):
        with session.begin():
            AgentClassificationResultService(
                session,
                settings,
            ).accept(
                request("Unknown.Tag")
            )


def test_agent_audit_uses_bot_identity(
    session,
    active_classification_rules,
) -> None:
    settings = Settings(
        agent_enabled=True,
        openmetadata_agent_bot_name=(
            "catalog-agent-bot"
        ),
    )

    with session.begin():
        AgentClassificationResultService(
            session,
            settings,
        ).accept(request())

    row = session.execute(
        select(AuditEvent)
    ).scalar_one()

    assert (
        row.actor_id
        == "bot:catalog-agent-bot"
    )
