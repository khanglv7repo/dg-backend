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
from app.tasks.policy_sync import sync_policy_to_ranger

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
    """Publish one durable outbox event to its real downstream consumer.

    The outbox is an at-least-once transport. A record is marked DISPATCHED
    only after Celery has accepted the reconciliation task. Any publish error,
    malformed payload, or unknown event type is raised so the caller leaves
    the record retryable instead of silently losing the event.
    """
    if record.event_type not in {
        "policy.version.activated",
        "policy.version.rolled_back",
    }:
        raise ValueError(
            f"unsupported outbox event_type: {record.event_type!r}"
        )

    payload = record.payload or {}
    policy_version_id = payload.get("policy_version_id")
    if not policy_version_id:
        raise ValueError(
            f"outbox event {record.id} missing policy_version_id"
        )

    task = sync_policy_to_ranger.delay(
        policy_version_id=str(policy_version_id),
        correlation_id=payload.get("correlation_id"),
    )
    logger.info(
        "outbox event published: id=%s aggregate_type=%s aggregate_id=%s "
        "event_type=%s task_id=%s",
        record.id,
        record.aggregate_type,
        record.aggregate_id,
        record.event_type,
        getattr(task, "id", None),
    )
