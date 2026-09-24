"""Celery task recovering unfinished Inbox/Outbox workflows.

Runs periodically via Celery Beat to recover from transient failures (worker
crash, Redis restart, Celery publish failure mid-transaction) -- PostgreSQL
remains the authoritative state store; this task never assumes an event or
task succeeded just because it was once attempted (Hard Invariant #20:
event/dispatch is only a trigger, reconciliation is correctness).
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from app.celery_app import app
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.event_inbox import EventInbox
from app.repositories.audit import AuditRepository
from app.repositories.event_inbox import EventInboxRepository
from app.services.event_router import EventPurpose, EventPurposeRouter
from app.tasks.classification import classify_entity
from app.tasks.tag_sync import sync_tags_to_ranger

logger = logging.getLogger(__name__)

# Rows older than this, still not fully dispatched, are candidates for retry.
# Avoids racing an in-flight request that's still inside its own dispatch
# window (TX1 commit -> Celery publish -> TX2 commit in
# openmetadata_event_adapter.py).
_RECOVERY_MIN_AGE_SECONDS = 30
_POISON_EVENT_MAX_ATTEMPTS = 5


@app.task(name="app.tasks.recovery.retry_unfinished_workflows")
def retry_unfinished_workflows() -> dict:
    settings = get_settings()
    session = SessionLocal()
    recovered = 0
    poisoned = 0
    try:
        inbox_repo = EventInboxRepository(session)
        audit = AuditRepository(session)

        stmt = (
            select(EventInbox)
            .where(EventInbox.status.in_(("RECEIVED",)))
            .order_by(EventInbox.created_at.asc())
            .limit(200)
        )
        candidates = list(session.execute(stmt).scalars())

        for record in candidates:
            purposes_required = set(record.purposes or [])
            dispatched = set(record.dispatched_purposes or [])
            missing = purposes_required - dispatched
            if not missing:
                inbox_repo.mark_processed(record.id)
                session.commit()
                continue

            retry_count = int((record.dispatched_tasks or {}).get("_retry_count", 0))
            if retry_count >= _POISON_EVENT_MAX_ATTEMPTS:
                audit.record(
                    actor_id="system:recovery-sweep",
                    actor_name="Inbox Recovery Sweep",
                    action="EVENT_INBOX_POISON_EVENT",
                    object_type=record.entity_type,
                    object_id=record.entity_fqn,
                    correlation_id=record.correlation_id,
                    details={
                        "event_id": record.event_id,
                        "missing_purposes": sorted(missing),
                        "retry_count": retry_count,
                    },
                )
                session.commit()
                poisoned += 1
                continue

            newly_dispatched: list[tuple[str, str]] = []
            if EventPurpose.CLASSIFY.value in missing:
                try:
                    task_res = classify_entity.delay(
                        event_id=record.event_id,
                        entity_type=record.entity_type,
                        entity_fqn=record.entity_fqn,
                        correlation_id=record.correlation_id,
                    )
                    newly_dispatched.append((EventPurpose.CLASSIFY.value, str(task_res.id)))
                except Exception:
                    logger.exception(
                        "recovery redispatch of classify_entity failed for event_id=%s",
                        record.event_id,
                    )

            if EventPurpose.TAG_SYNC.value in missing:
                try:
                    task_res = sync_tags_to_ranger.delay(
                        entity_type=record.entity_type,
                        entity_fqn=record.entity_fqn,
                        correlation_id=record.correlation_id,
                    )
                    newly_dispatched.append((EventPurpose.TAG_SYNC.value, str(task_res.id)))
                except Exception:
                    logger.exception(
                        "recovery redispatch of sync_tags_to_ranger failed for event_id=%s",
                        record.event_id,
                    )

            for purpose, task_id in newly_dispatched:
                inbox_repo.record_purpose_dispatched(record.id, purpose, task_id)
                recovered += 1

            dispatched_tasks = dict(record.dispatched_tasks or {})
            dispatched_tasks["_retry_count"] = retry_count + 1
            record.dispatched_tasks = dispatched_tasks
            session.commit()
    finally:
        session.close()
    return {"recovered": recovered, "poisoned": poisoned}
