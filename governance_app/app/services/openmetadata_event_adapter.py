"""Adapter for raw OpenMetadata ChangeEvent webhooks using EventInbox and EventPurposeRouter.

Per R3 target flow & transaction fence:
- TX1: Persist event_inbox record -> session.commit()
- Celery Task Publication (outside TX1, after commit)
- TX2: Record dispatched tasks & update status -> session.commit()
- Partial / Broker Failure Recovery: Duplicate deliveries check undispatched purposes and publish missing tasks.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AuthorizationError
from app.repositories.audit import AuditRepository
from app.repositories.event_inbox import EventInboxRepository
from app.services.event_router import EventPurpose, EventPurposeRouter
from app.tasks.classification import classify_entity
from app.tasks.tag_sync import sync_tags_to_ranger

logger = logging.getLogger(__name__)


class OpenMetadataEventAdapterService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.audit = AuditRepository(session)
        self.inbox = EventInboxRepository(session)

    def verify_webhook_token(self, token_or_header: str | None) -> None:
        if self.settings.openmetadata_webhook_secret:
            expected = self.settings.openmetadata_webhook_secret.get_secret_value()
            if not token_or_header or token_or_header.strip() != expected.strip():
                raise AuthorizationError(
                    "Invalid OpenMetadata webhook authentication secret"
                )

    def process_change_event(self, event_data: dict[str, Any]) -> dict[str, Any]:
        event_id = str(event_data.get("id") or event_data.get("eventId") or "")
        event_type = str(event_data.get("eventType") or "")
        entity_type = str(event_data.get("entityType") or "table")
        entity_fqn = str(
            event_data.get("entityFullyQualifiedName")
            or event_data.get("entityFQN")
            or event_data.get("entity", {}).get("fullyQualifiedName")
            or ""
        )
        timestamp = event_data.get("timestamp") or 0
        correlation_id = f"om-event-{event_id}" if event_id else None

        if not entity_fqn:
            logger.info("Ignoring OpenMetadata event with missing entityFullyQualifiedName")
            return {"status": "ignored", "reason": "missing_entity_fqn"}

        if not event_id:
            event_id = f"evt-{timestamp}-{hash(entity_fqn)}"

        purposes = EventPurposeRouter.route(event_data)
        purpose_strings = sorted(p.value for p in purposes)

        # ---------------------------------------------------------------------
        # TX1: Persist event_inbox record to DB and COMMIT before Celery publish
        # ---------------------------------------------------------------------
        inbox_record, is_duplicate = self.inbox.record_event(
            event_id=event_id,
            event_type=event_type,
            entity_type=entity_type,
            entity_fqn=entity_fqn,
            payload=event_data,
            purposes=purpose_strings,
            correlation_id=correlation_id,
        )

        # Commit TX1 so that independent sessions / background tasks can see inbox_record immediately
        self.session.commit()

        dispatched_purposes = set(inbox_record.dispatched_purposes or [])
        already_fully_dispatched = is_duplicate and set(purpose_strings).issubset(dispatched_purposes)

        if already_fully_dispatched:
            logger.info("Duplicate event %s already fully dispatched", event_id)
            self.audit.record(
                actor_id="system:openmetadata-webhook",
                actor_name="OpenMetadata Webhook Adapter",
                action="EVENT_INBOX_DUPLICATE_SKIPPED",
                object_type=entity_type,
                object_id=entity_fqn,
                correlation_id=correlation_id,
                details={"event_id": event_id, "status": "duplicate"},
            )
            self.session.commit()
            return {"status": "duplicate", "event_id": event_id}

        # ---------------------------------------------------------------------
        # Publish Celery tasks ONLY after TX1 has been committed
        # ---------------------------------------------------------------------
        dispatched_tasks: list[str] = []
        newly_dispatched_purposes: list[str] = []

        if EventPurpose.CLASSIFY in purposes and EventPurpose.CLASSIFY.value not in dispatched_purposes:
            try:
                task_res = classify_entity.delay(
                    event_id=event_id,
                    entity_type=entity_type,
                    entity_fqn=entity_fqn,
                    correlation_id=correlation_id,
                )
                dispatched_tasks.append(str(task_res.id))
                newly_dispatched_purposes.append(EventPurpose.CLASSIFY.value)
                self.audit.record(
                    actor_id="system:openmetadata-webhook",
                    actor_name="OpenMetadata Webhook Adapter",
                    action="ASSET_CLASSIFICATION_DISPATCHED",
                    object_type=entity_type,
                    object_id=entity_fqn,
                    correlation_id=correlation_id,
                    details={"event_id": event_id, "purposes": purpose_strings, "task_id": str(task_res.id)},
                )
            except Exception as exc:
                logger.warning("Could not dispatch classify_entity task: %s", exc)

        if EventPurpose.TAG_SYNC in purposes and EventPurpose.TAG_SYNC.value not in dispatched_purposes:
            try:
                task_res = sync_tags_to_ranger.delay(
                    entity_type=entity_type,
                    entity_fqn=entity_fqn,
                    correlation_id=correlation_id,
                )
                dispatched_tasks.append(str(task_res.id))
                newly_dispatched_purposes.append(EventPurpose.TAG_SYNC.value)
                self.audit.record(
                    actor_id="system:openmetadata-webhook",
                    actor_name="OpenMetadata Webhook Adapter",
                    action="RANGER_TAG_SYNC_DISPATCHED",
                    object_type=entity_type,
                    object_id=entity_fqn,
                    correlation_id=correlation_id,
                    details={"event_id": event_id, "purposes": purpose_strings, "task_id": str(task_res.id)},
                )
            except Exception as exc:
                logger.warning("Could not dispatch sync_tags_to_ranger task: %s", exc)

        # ---------------------------------------------------------------------
        # TX2: Record successful dispatch state in DB and COMMIT
        # ---------------------------------------------------------------------
        for purpose, task_id in zip(newly_dispatched_purposes, dispatched_tasks):
            self.inbox.record_purpose_dispatched(inbox_record.id, purpose, task_id)

        # Mark processed if all required purposes were dispatched (or no work required)
        updated_dispatched = set(inbox_record.dispatched_purposes or [])
        if set(purpose_strings).issubset(updated_dispatched) or not purpose_strings:
            self.inbox.mark_processed(inbox_record.id)

        self.session.commit()

        return {
            "status": "accepted",
            "event_id": event_id,
            "purposes": purpose_strings,
            "dispatched_tasks": dispatched_tasks,
        }
