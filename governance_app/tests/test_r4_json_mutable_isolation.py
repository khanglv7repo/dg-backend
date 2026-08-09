from __future__ import annotations


def test_r4_mutable_json_does_not_instrument_legacy_list_columns() -> None:
    """R4 MutableDict must not leak onto the shared legacy JSON type."""

    from app.models.classification import ClassificationRun
    from app.models.event_inbox import EventInbox

    run = ClassificationRun(
        event_id="evt-r4-mutable-isolation",
        entity_type="table",
        entity_fqn="catalog.schema.table",
        source_kind="DETERMINISTIC",
        source_version="rules-v1",
        outcome="NO_MATCH",
        action="NONE",
        suggestions=[],
        evidence={},
        openmetadata_suggestion_ids=[],
    )
    inbox = EventInbox(
        event_id="evt-r4-mutable-isolation",
        event_type="ENTITY_UPDATED",
        entity_type="table",
        entity_fqn="catalog.schema.table",
        payload={},
        purposes=["CLASSIFY", "TAG_SYNC"],
        dispatched_purposes=[],
        dispatched_tasks={},
    )

    assert run.suggestions == []
    assert run.openmetadata_suggestion_ids == []
    assert inbox.purposes == ["CLASSIFY", "TAG_SYNC"]
    assert inbox.dispatched_purposes == []
