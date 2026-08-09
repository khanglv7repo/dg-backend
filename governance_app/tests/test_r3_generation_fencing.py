"""Unit tests exercising production generation fencing methods on ClassificationExecutionRepository."""
from __future__ import annotations

from app.repositories.classification_execution import ClassificationExecutionRepository


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
