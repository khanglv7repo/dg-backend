"""Repository for durable EventInbox storage and idempotency check."""
from __future__ import annotations

from typing import Any
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.event_inbox import EventInbox


class EventInboxRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_event_id(self, event_id: str) -> EventInbox | None:
        return (
            self.session.query(EventInbox)
            .filter(EventInbox.event_id == event_id)
            .first()
        )

    def record_event(
        self,
        *,
        event_id: str,
        event_type: str,
        entity_type: str,
        entity_fqn: str,
        payload: dict[str, Any],
        purposes: list[str],
        correlation_id: str | None = None,
    ) -> tuple[EventInbox, bool]:
        """Record incoming webhook event.

        Returns (event_inbox_record, is_duplicate).
        """
        existing = self.get_by_event_id(event_id)
        if existing is not None:
            return existing, True

        record = EventInbox(
            event_id=event_id,
            event_type=event_type,
            entity_type=entity_type,
            entity_fqn=entity_fqn,
            payload=payload,
            purposes=purposes,
            dispatched_purposes=[],
            dispatched_tasks={},
            status="RECEIVED",
            correlation_id=correlation_id,
        )
        try:
            self.session.add(record)
            self.session.flush()
            return record, False
        except IntegrityError:
            self.session.rollback()
            existing = self.get_by_event_id(event_id)
            if existing is not None:
                return existing, True
            raise

    def record_purpose_dispatched(self, record_id: Any, purpose: str, task_id: str) -> EventInbox | None:
        record = self.session.get(EventInbox, record_id)
        if record:
            current_purposes = list(record.dispatched_purposes or [])
            if purpose not in current_purposes:
                current_purposes.append(purpose)
                record.dispatched_purposes = current_purposes

            current_tasks = dict(record.dispatched_tasks or {})
            current_tasks[purpose] = task_id
            record.dispatched_tasks = current_tasks

            # Check if all required purposes have been dispatched
            required = set(record.purposes or [])
            dispatched = set(current_purposes)
            if required.issubset(dispatched):
                record.status = "DISPATCHED"

            self.session.flush()
        return record

    def mark_processed(self, record_id: Any) -> None:
        record = self.session.get(EventInbox, record_id)
        if record:
            record.status = "PROCESSED"
            self.session.flush()
