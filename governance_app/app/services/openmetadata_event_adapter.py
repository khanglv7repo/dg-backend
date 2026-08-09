"""Adapter for raw OpenMetadata ChangeEvent webhooks using EventInbox and EventPurposeRouter.

Per R3 target flow:
- Webhook is a trigger, NOT business truth.
- Persists to event_inbox for idempotency & audit.
- Evaluates EventPurposeRouter -> {CLASSIFY}, {TAG_SYNC}, both, or none.
- Dispatches Celery tasks accordingly.
- Tag-only changes route strictly to {TAG_SYNC} (prevents loops!).
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

        inbox_record, is_duplicate = self.inbox.record_event(
            event_id=event_id,
            event_type=event_type,
            entity_type=entity_type,
            entity_fqn=entity_fqn,
            payload=event_data,
            purposes=purpose_strings,
            correlation_id=correlation_id,
        )

        if is_duplicate:
            logger.info("Duplicate event %s skipped", event_id)
            self.audit.record(
                actor_id="system:openmetadata-webhook",
                actor_name="OpenMetadata Webhook Adapter",
                action="EVENT_INBOX_DUPLICATE_SKIPPED",
                object_type=entity_type,
                object_id=entity_fqn,
                correlation_id=correlation_id,
                details={"event_id": event_id, "status": "duplicate"},
            )
            return {"status": "duplicate", "event_id": event_id}

        dispatched_tasks: list[str] = []

        if EventPurpose.CLASSIFY in purposes:
            # Dispatch classification task
            try:
                task_res = classify_entity.delay(
                    event_id=event_id,
                    entity_type=entity_type,
                    entity_fqn=entity_fqn,
                    correlation_id=correlation_id,
                )
                dispatched_tasks.append(str(task_res.id))
            except Exception as exc:
                logger.warning("Could not dispatch classify_entity task: %s", exc)

            self.audit.record(
                actor_id="system:openmetadata-webhook",
                actor_name="OpenMetadata Webhook Adapter",
                action="ASSET_CLASSIFICATION_DISPATCHED",
                object_type=entity_type,
                object_id=entity_fqn,
                correlation_id=correlation_id,
                details={"event_id": event_id, "purposes": purpose_strings},
            )

        if EventPurpose.TAG_SYNC in purposes:
            # Dispatch tag sync task (ranger.tag-sync queue, concurrency 1)
            try:
                task_res = sync_tags_to_ranger.delay(
                    entity_type=entity_type,
                    entity_fqn=entity_fqn,
                    correlation_id=correlation_id,
                )
                dispatched_tasks.append(str(task_res.id))
            except Exception as exc:
                logger.warning("Could not dispatch sync_tags_to_ranger task: %s", exc)

            self.audit.record(
                actor_id="system:openmetadata-webhook",
                actor_name="OpenMetadata Webhook Adapter",
                action="RANGER_TAG_SYNC_DISPATCHED",
                object_type=entity_type,
                object_id=entity_fqn,
                correlation_id=correlation_id,
                details={"event_id": event_id, "purposes": purpose_strings},
            )

        self.inbox.mark_processed(inbox_record.id)

        return {
            "status": "accepted",
            "event_id": event_id,
            "purposes": purpose_strings,
            "dispatched_tasks": dispatched_tasks,
        }
