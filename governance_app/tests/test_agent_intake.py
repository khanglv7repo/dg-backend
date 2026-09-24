import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy import select

from app.core.config import Settings
from app.core.errors import ConfigurationError
from app.models.audit import AuditEvent
from app.models.enums import (
    ClassificationAction,
)
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
    """After the GovernanceJob→Celery migration, AgentClassificationResultService
    dispatches CREATE_OM_SUGGESTIONS via create_om_suggestions.delay() instead of
    JobRepository.enqueue(). Verify the Celery task fires with AGENT source_kind.
    """
    settings = Settings(agent_enabled=True)

    fake_async = MagicMock()
    fake_async.id = "00000000-0000-0000-0000-000000000021"

    with patch(
        "app.tasks.classification.create_om_suggestions.delay",
        return_value=fake_async,
    ) as mock_delay:
        with session.begin():
            result = AgentClassificationResultService(session, settings).accept(request())

    assert result["action"] == ClassificationAction.OPENMETADATA_SUGGESTION.value
    mock_delay.assert_called_once()
    call_kwargs = mock_delay.call_args.kwargs
    assert call_kwargs["payload"]["source_kind"] == "AGENT"


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
