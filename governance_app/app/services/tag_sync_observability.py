"""Bounded read-only evidence for the OpenMetadata-to-Ranger tag flow."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.clients.ranger_tags import RangerTagStoreClient
from app.models.event_inbox import EventInbox
from app.repositories.tag_sync_state import TagSyncStateRepository


class TagSyncObservabilityService:
    """Join durable webhook/task state with a current Ranger tag read-back."""

    _MAX_EVENTS = 20

    def __init__(self, session: Session, *, tag_store: RangerTagStoreClient) -> None:
        self.session = session
        self.tag_store = tag_store
        self.sync_states = TagSyncStateRepository(session)

    def inspect(self, *, entity_type: str, entity_fqn: str) -> dict[str, Any]:
        events = (
            self.session.query(EventInbox)
            .filter(
                EventInbox.entity_type == entity_type,
                EventInbox.entity_fqn == entity_fqn,
            )
            .order_by(EventInbox.created_at.desc())
            .limit(self._MAX_EVENTS)
            .all()
        )
        event_evidence = [self._event_evidence(event) for event in events]
        tag_sync_dispatched = any(
            item["tag_sync_dispatched"] for item in event_evidence
        )

        state = self.sync_states.get_by_entity(entity_type, entity_fqn)
        expected = self._expected_assignments(state.details if state else {})
        actual = self.tag_store.read_actual_state(entity_fqn)
        assignments = [
            {"field_path": field_path, "tag": tag}
            for field_path, tag in sorted(actual)
        ]
        matches_durable_snapshot = (
            expected == actual if state is not None else None
        )
        synchronized = bool(
            state is not None
            and state.status == "SYNCHRONIZED"
            and matches_durable_snapshot is True
        )

        return {
            "entity_type": entity_type,
            "entity_fqn": entity_fqn,
            "webhook": {
                "received": bool(events),
                "events": event_evidence,
            },
            "tag_sync_dispatch": {
                "dispatched": tag_sync_dispatched,
                "note": "EventInbox PROCESSED means dispatch completed, not Ranger synchronization.",
            },
            "tag_sync_state": (
                {
                    "found": True,
                    "status": state.status,
                    "checksum": state.checksum,
                    "updated_at": state.updated_at,
                }
                if state is not None
                else {"found": False}
            ),
            "ranger_read_back": {
                "assignments": assignments,
                "matches_durable_snapshot": matches_durable_snapshot,
            },
            "synchronized": synchronized,
        }

    @staticmethod
    def _event_evidence(event: EventInbox) -> dict[str, Any]:
        dispatched = set(event.dispatched_purposes or [])
        tasks = dict(event.dispatched_tasks or {})
        return {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "status": event.status,
            "purposes": list(event.purposes or []),
            "tag_sync_dispatched": "TAG_SYNC" in dispatched,
            "tag_sync_task_id": tasks.get("TAG_SYNC"),
            "received_at": event.created_at,
        }

    @staticmethod
    def _expected_assignments(details: dict[str, Any]) -> set[tuple[str, str]]:
        expected: set[tuple[str, str]] = set()
        for tag in details.get("entity_tags", []) or []:
            expected.add(("$entity", str(tag)))
        for field_path, tags in (details.get("field_tags") or {}).items():
            for tag in tags or []:
                expected.add((str(field_path), str(tag)))
        return expected
