"""Repository for the transactional Outbox (docs/13_IMPLEMENTATION_SPEC.md section 3).

Callers must enqueue an outbox row inside the SAME DB transaction as the
business write it accompanies (e.g. `session.begin()` block that also writes
`DataAccessPolicyVersion`), so a business commit and its outbound event either
both happen or neither does. A separate dispatcher (app/tasks/outbox.py) later
drains PENDING rows and publishes them; publish failures never lose the event
because the row remains PENDING until dispatch succeeds.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.event_outbox import EventOutbox
from app.models.job import utcnow


class EventOutboxRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def enqueue(
        self,
        *,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> EventOutbox:
        record = EventOutbox(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=payload,
            status="PENDING",
            dispatch_attempts=0,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def claim_pending(self, *, batch_size: int) -> list[EventOutbox]:
        """Claim PENDING rows for one dispatcher transaction.

        PostgreSQL uses FOR UPDATE SKIP LOCKED so overlapping Celery deliveries
        cannot publish the same row concurrently. A crash after publish but
        before mark_dispatched can still cause a later duplicate, which is the
        intentional at-least-once transport contract.
        """
        stmt = (
            select(EventOutbox)
            .where(EventOutbox.status == "PENDING")
            .order_by(EventOutbox.created_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        return list(self.session.execute(stmt).scalars())

    def mark_dispatched(self, record_id: Any) -> None:
        record = self.session.get(EventOutbox, record_id)
        if record:
            record.status = "DISPATCHED"
            record.dispatched_at = utcnow()
            self.session.flush()

    def mark_failed_attempt(self, record_id: Any, *, max_attempts: int) -> None:
        record = self.session.get(EventOutbox, record_id)
        if record:
            record.dispatch_attempts += 1
            if record.dispatch_attempts >= max_attempts:
                record.status = "FAILED"
            self.session.flush()
