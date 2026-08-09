"""Unit tests exercising production generation fencing methods on ClassificationExecutionRepository."""
from __future__ import annotations

from app.repositories.classification_execution import ClassificationExecutionRepository
from app.models.classification_execution import ClassificationExecution
from sqlalchemy.exc import IntegrityError


def test_production_generation_auto_increment_and_superseding(session) -> None:
    repo = ClassificationExecutionRepository(session)

    # First classification run creates generation 1
    exec1 = repo.create_next_generation(
        event_id="evt-gen-1",
        entity_type="table",
        entity_fqn="sales.orders",
        status="WAITING_AI",
    )

    assert exec1.generation >= 1
    assert exec1.status == "WAITING_AI"

    # Second classification run automatically creates generation N+1 and supersedes older WAITING_AI gen
    exec2 = repo.create_next_generation(
        event_id="evt-gen-2",
        entity_type="table",
        entity_fqn="sales.orders",
        status="EVALUATING",
    )

    assert exec2.generation == exec1.generation + 1
    assert exec2.status == "EVALUATING"

    # Verify older generation was automatically marked SUPERSEDED by production repository
    stale_rec = repo.get(exec1.id)
    assert stale_rec.status == "SUPERSEDED"
    assert repo.is_current_generation(exec1.id, exec1.generation) is False, "Stale generation MUST NOT be current generation!"
    assert repo.is_current_generation(exec2.id, exec2.generation) is True


def test_generation_unique_constraint_rejects_duplicate_generation(session) -> None:
    repo = ClassificationExecutionRepository(session)
    repo.create(
        event_id="evt-a",
        entity_type="table",
        entity_fqn="sales.customers",
        generation=1,
    )

    try:
        repo.create(
            event_id="evt-b",
            entity_type="table",
            entity_fqn="sales.customers",
            generation=1,
        )
        assert False, "Duplicate entity_fqn/generation must violate the DB fence"
    except IntegrityError:
        session.rollback()

    assert session.query(ClassificationExecution).count() == 0


def test_duplicate_event_entity_unique_constraint_rejects_second_execution(session) -> None:
    repo = ClassificationExecutionRepository(session)
    repo.create(
        event_id="evt-same",
        entity_type="table",
        entity_fqn="sales.customers",
        generation=1,
    )

    try:
        repo.create(
            event_id="evt-same",
            entity_type="table",
            entity_fqn="sales.customers",
            generation=2,
        )
        assert False, "Duplicate event/entity must violate the idempotency fence"
    except IntegrityError:
        session.rollback()
