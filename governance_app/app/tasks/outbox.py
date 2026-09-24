"""Celery task draining the transactional Outbox (event_outbox table).

Business writes enqueue a PENDING row in the same DB transaction as their
authoritative write (Hard Invariant: a business commit and its outbound
event either both happen or neither does). This task periodically claims
PENDING rows and publishes them; a publish failure leaves the row PENDING
(or FAILED after max attempts) rather than losing the event.
"""
from __future__ import annotations

import logging

from app.celery_app import app
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.repositories.event_outbox import EventOutboxRepository

logger = logging.getLogger(__name__)


@app.task(name="app.tasks.outbox.dispatch_pending_outbox_events")
def dispatch_pending_outbox_events() -> dict:
    settings = get_settings()
    session = SessionLocal()
    dispatched = 0
    failed = 0
    try:
        repository = EventOutboxRepository(session)
        pending = repository.claim_pending(batch_size=50)
        for record in pending:
            try:
                _publish(record)
                repository.mark_dispatched(record.id)
                dispatched += 1
            except Exception:
                logger.exception(
                    "outbox dispatch failed for event_outbox id=%s event_type=%s",
                    record.id,
                    record.event_type,
                )
                repository.mark_failed_attempt(
                    record.id,
                    max_attempts=settings.outbox_max_dispatch_attempts,
                )
                failed += 1
        session.commit()
    finally:
        session.close()
    return {"dispatched": dispatched, "failed": failed}


def _publish(record) -> None:
    """Publish a single outbox event. Placeholder transport: log-only until a
    concrete downstream consumer (e.g. an outbound webhook or message bus) is
    selected -- no such external contract is frozen in
    docs/13_IMPLEMENTATION_SPEC.md beyond "the event was durably enqueued and
    is dispatched exactly like an Inbox event is processed", so this task's
    job is to guarantee delivery attempts happen, not to invent a new
    external wire format.
    """
    logger.info(
        "outbox event dispatched: aggregate_type=%s aggregate_id=%s event_type=%s",
        record.aggregate_type,
        record.aggregate_id,
        record.event_type,
    )
