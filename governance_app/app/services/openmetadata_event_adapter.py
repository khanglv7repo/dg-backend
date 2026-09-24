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
from app.tasks.tag_override_guard import NATIVE_CLASSIFIER_ACTORS, restore_overridden_tag
from app.tasks.tag_sync import sync_tags_to_ranger
from app.tasks.task_resolution import resolve_task_followup

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

        # Task ChangeEvents (task.entityUpdated) are a distinct domain from
        # column/tag ChangeEvents -- route them separately, always
        # re-fetching the authoritative Task status (A5 PARTIAL finding:
        # this event type never carries the status field itself).
        if event_type == "task.entityUpdated":
            task_entity_id = str(
                event_data.get("entityId") or event_data.get("entity", {}).get("id") or ""
            )
            if task_entity_id:
                task_res = resolve_task_followup.delay(
                    task_id=task_entity_id,
                    correlation_id=correlation_id,
                )
                return {
                    "status": "accepted",
                    "event_id": event_id or task_entity_id,
                    "purposes": [],
                    "dispatched_tasks": [str(task_res.id)] if getattr(task_res, "id", None) else [],
                }
            logger.info("Ignoring task.entityUpdated event with missing entityId")
            return {"status": "ignored", "reason": "missing_task_entity_id"}

        if not entity_fqn:
            logger.info("Ignoring OpenMetadata event with missing entityFullyQualifiedName")
            return {"status": "ignored", "reason": "missing_entity_fqn"}

        if not event_id:
            event_id = f"evt-{timestamp}-{hash(entity_fqn)}"

        # C4 conservative fallback (BLOCKED -> auto-restore-on-override,
        # docs/13_IMPLEMENTATION_SPEC.md section 9): a tag removed by an
        # actor identified as OM's native classifier is treated as an
        # unauthorized override and restored immediately.
        self._maybe_restore_overridden_tags(
            event_data,
            entity_type=entity_type,
            entity_fqn=entity_fqn,
            correlation_id=correlation_id,
        )

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

    @staticmethod
    def _maybe_restore_overridden_tags(
        event_data: dict[str, Any],
        *,
        entity_type: str,
        entity_fqn: str,
        correlation_id: str | None,
    ) -> None:
        actor = str(event_data.get("userName") or "").strip()
        if actor not in NATIVE_CLASSIFIER_ACTORS:
            return

        change_desc = event_data.get("changeDescription") or {}
        for change in change_desc.get("fieldsDeleted", []) or []:
            if not isinstance(change, dict):
                continue
            name = str(change.get("name") or "")
            old_value = change.get("oldValue")
            removed_tag_fqns = OpenMetadataEventAdapterService._extract_tag_fqns(old_value)
            if not removed_tag_fqns:
                continue

            field_path = (
                name.split(".", 1)[1] if name.startswith("columns.") else None
            )
            restore_overridden_tag.delay(
                entity_type=entity_type,
                entity_fqn=entity_fqn,
                removed_tag_fqns=removed_tag_fqns,
                field_path=field_path,
                correlation_id=correlation_id,
            )

    @staticmethod
    def _extract_tag_fqns(value: Any) -> list[str]:
        """Extract tagFQN values from a ChangeEvent oldValue, which may be a
        JSON-encoded string, a raw TagLabel dict, or a list of TagLabel
        dicts (C3's confirmed TagLabel schema).
        """
        import json as _json

        if isinstance(value, str):
            try:
                value = _json.loads(value)
            except ValueError:
                return []

        if isinstance(value, dict):
            value = [value]

        if not isinstance(value, list):
            return []

        return [
            str(item["tagFQN"])
            for item in value
            if isinstance(item, dict) and item.get("tagFQN")
        ]
