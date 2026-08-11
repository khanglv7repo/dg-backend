from __future__ import annotations

import tempfile
from unittest.mock import patch

import anyio
import pytest
from fastmcp import Client
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401
from app.core.config import Settings
from app.core.errors import ConflictError, ValidationError
from app.db.base import Base
from app.mcp import backend_mcp_server
from app.models.audit import AuditEvent
from app.repositories.classification_execution import ClassificationExecutionRepository
from app.services.classification_completion import ClassificationCompletionService

ENTITY_FQN = "financial_postgres.financial_db.analytics.customer_360"
FIELD_PATH = f"{ENTITY_FQN}.phone_number"


def apply_result(
    *,
    tag: str = "PII.Phone",
    rationale: str = "Phone-like field should be governed as PII.Phone.",
    confidence: float = 0.95,
    mutation_status: str = "APPLIED",
) -> dict:
    return {
        "entity_type": "table",
        "entity_fqn": ENTITY_FQN,
        "recommendations": [
            {
                "tag": tag,
                "confidence": confidence,
                "rationale": rationale,
                "field_path": FIELD_PATH,
                "action_recommendation": "APPLY",
            }
        ],
        "mutations": [
            {
                "status": mutation_status,
                "entity_fqn": ENTITY_FQN,
                "field_path": FIELD_PATH,
                "tag_fqn": tag,
                "mutation_count": 1 if mutation_status == "APPLIED" else 0,
                **({"transport": "NATIVE_API"} if mutation_status == "APPLIED" else {}),
            }
        ],
    }


def no_proposal_result() -> dict:
    return {
        "entity_type": "table",
        "entity_fqn": ENTITY_FQN,
        "recommendations": [],
        "mutations": [],
    }


def create_waiting(session, *, event_id: str = "evt-r6b"):
    repo = ClassificationExecutionRepository(session)
    record = repo.create_next_generation(
        event_id=event_id,
        entity_type="table",
        entity_fqn=ENTITY_FQN,
        status="WAITING_AI",
        outcome="NO_MATCH",
        correlation_id=event_id,
    )
    session.commit()
    return record


def complete(session, record, *, status: str = "COMPLETED", result: dict | None = None):
    with session.begin():
        return ClassificationCompletionService(session).complete(
            execution_id=str(record.id),
            generation=record.generation,
            status=status,
            result=result if result is not None else apply_result(),
            actor_id="backend-mcp-test",
            actor_name="Backend MCP Test",
        )


def test_current_waiting_ai_generation_completes_atomically(session) -> None:
    record = create_waiting(session)

    response = complete(session, record)

    assert response["status"] == "COMPLETED"
    assert response["authority_changed"] is True
    assert response["duplicate"] is False
    assert response["stale"] is False
    assert response["recommendation_count"] == 1
    assert response["om_mutation_count"] == 1

    persisted = ClassificationExecutionRepository(session).get(record.id)
    assert persisted is not None
    assert persisted.status == "COMPLETED"
    assert persisted.outcome == "NO_MATCH"
    assert persisted.confidence == pytest.approx(0.95)
    assert persisted.suggestions[0]["tag"] == "PII.Phone"
    completion = persisted.evidence["ai_completion"]
    assert completion["generation"] == record.generation
    assert completion["status"] == "COMPLETED"
    assert completion["decision_fingerprint"] == response["decision_fingerprint"]

    audits = session.query(AuditEvent).filter(
        AuditEvent.action == "AI_CLASSIFICATION_COMPLETED"
    ).all()
    assert len(audits) == 1
    assert audits[0].details["generation"] == record.generation


def test_semantically_same_retry_is_idempotent_even_if_om_now_no_change(session) -> None:
    record = create_waiting(session)
    first = complete(session, record)

    retry_result = apply_result(
        rationale="Retry can produce different prose but the same governed target.",
        confidence=0.93,
        mutation_status="NO_CHANGE",
    )
    second = complete(session, record, result=retry_result)

    assert second["status"] == "COMPLETED"
    assert second["authority_changed"] is False
    assert second["duplicate"] is True
    assert second["decision_fingerprint"] == first["decision_fingerprint"]
    assert session.query(AuditEvent).filter(
        AuditEvent.action == "AI_CLASSIFICATION_COMPLETED"
    ).count() == 1


def test_conflicting_duplicate_completion_fails_closed(session) -> None:
    record = create_waiting(session)
    complete(session, record)

    conflicting = apply_result(tag="PII.Email")
    with pytest.raises(ConflictError):
        complete(session, record, result=conflicting)

    persisted = ClassificationExecutionRepository(session).get(record.id)
    assert persisted.status == "COMPLETED"
    assert persisted.suggestions[0]["tag"] == "PII.Phone"


def test_stale_generation_has_zero_authority_change(session) -> None:
    first = create_waiting(session, event_id="evt-r6b-1")
    second = ClassificationExecutionRepository(session).create_next_generation(
        event_id="evt-r6b-2",
        entity_type="table",
        entity_fqn=ENTITY_FQN,
        status="WAITING_AI",
        outcome="NO_MATCH",
        correlation_id="evt-r6b-2",
    )
    session.commit()

    with session.begin():
        response = ClassificationCompletionService(session).complete(
            execution_id=str(first.id),
            generation=first.generation,
            status="COMPLETED",
            result=apply_result(),
            actor_id="backend-mcp-test",
            actor_name="Backend MCP Test",
        )

    assert response["status"] == "SUPERSEDED"
    assert response["authority_changed"] is False
    assert response["stale"] is True
    assert response["current_generation"] == second.generation
    assert ClassificationExecutionRepository(session).get(first.id).status == "SUPERSEDED"
    assert ClassificationExecutionRepository(session).get(second.id).status == "WAITING_AI"
    assert session.query(AuditEvent).filter(
        AuditEvent.action == "AI_CLASSIFICATION_COMPLETED"
    ).count() == 0


def test_no_proposal_completes_same_generation_without_om_evidence(session) -> None:
    record = create_waiting(session)

    response = complete(
        session,
        record,
        status="NO_PROPOSAL",
        result=no_proposal_result(),
    )

    assert response["status"] == "NO_PROPOSAL"
    assert response["authority_changed"] is True
    assert response["recommendation_count"] == 0
    assert response["om_mutation_count"] == 0
    persisted = ClassificationExecutionRepository(session).get(record.id)
    assert persisted.status == "NO_PROPOSAL"
    assert persisted.suggestions == []
    assert persisted.evidence["ai_completion"]["status"] == "NO_PROPOSAL"
    assert session.query(AuditEvent).filter(
        AuditEvent.action == "AI_CLASSIFICATION_NO_PROPOSAL"
    ).count() == 1


def test_result_identity_must_match_execution(session) -> None:
    record = create_waiting(session)
    result = apply_result()
    result["entity_fqn"] = "other.service.db.schema.table"
    result["mutations"][0]["entity_fqn"] = result["entity_fqn"]

    with pytest.raises(ValidationError):
        complete(session, record, result=result)

    assert ClassificationExecutionRepository(session).get(record.id).status == "WAITING_AI"


def test_mcp_completion_tool_uses_shared_backend_state_without_confirmation() -> None:
    tmpdir = tempfile.TemporaryDirectory()
    engine = create_engine(
        f"sqlite+pysqlite:///{tmpdir.name}/r6b-completion.db",
        connect_args={"check_same_thread": False},
    )
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    settings = Settings(
        app_env="test",
        mcp_enabled=True,
        mcp_actor_id="r6b-agent",
        mcp_actor_name="R6B Agent",
    )

    try:
        with factory() as db:
            record = ClassificationExecutionRepository(db).create_next_generation(
                event_id="evt-mcp-r6b",
                entity_type="table",
                entity_fqn=ENTITY_FQN,
                status="WAITING_AI",
                outcome="NO_MATCH",
                correlation_id="evt-mcp-r6b",
            )
            db.commit()
            execution_id = str(record.id)
            generation = record.generation

        async def run() -> None:
            with patch.object(backend_mcp_server, "SessionLocal", factory), patch.object(
                backend_mcp_server,
                "get_settings",
                return_value=settings,
            ):
                async with Client(backend_mcp_server.mcp) as client:
                    response = await client.call_tool(
                        "complete_classification_execution",
                        {
                            "execution_id": execution_id,
                            "generation": generation,
                            "status": "COMPLETED",
                            "result": apply_result(),
                        },
                    )
                    assert response.data["status"] == "COMPLETED"
                    assert response.data["authority_changed"] is True
                    assert response.data["generation"] == generation

        anyio.run(run)

        with factory() as verify:
            persisted = ClassificationExecutionRepository(verify).get(execution_id)
            assert persisted.status == "COMPLETED"
            assert persisted.evidence["ai_completion"]["status"] == "COMPLETED"
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
        tmpdir.cleanup()
