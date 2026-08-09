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
        If event_id already exists, returns existing record and True.
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

    def mark_processed(self, record_id: Any) -> None:
        record = self.session.get(EventInbox, record_id)
        if record:
            record.status = "PROCESSED"
            self.session.flush()
