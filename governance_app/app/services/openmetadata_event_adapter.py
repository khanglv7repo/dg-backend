from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AuthorizationError
from app.models.enums import JobType
from app.repositories.audit import AuditRepository
from app.repositories.jobs import JobRepository
from app.schemas.events import MetadataEventRequest, MetadataField

logger = logging.getLogger(__name__)


class OpenMetadataEventAdapterService:
    """Adapter for raw OpenMetadata ChangeEvent webhooks.

    A ChangeEvent is treated as a trigger, not as the Ranger source of truth.
    When a tag change is detected we enqueue a RECONCILE_RANGER job that will
    read the current Confirmed tag state back from OpenMetadata before mapping
    anything to Ranger.
    """

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.audit = AuditRepository(session)
        self.jobs = JobRepository(session)

    def verify_webhook_token(self, token_or_header: str | None) -> None:
        if self.settings.openmetadata_webhook_secret:
            expected = self.settings.openmetadata_webhook_secret.get_secret_value()
            if not token_or_header or token_or_header.strip() != expected.strip():
                raise AuthorizationError("Invalid OpenMetadata webhook authentication secret")

    def process_change_event(self, event_data: dict[str, Any]) -> list[str]:
        """Process one raw OpenMetadata ChangeEvent dictionary."""

        event_id = str(event_data.get("id") or event_data.get("eventId") or "")
        event_type = str(event_data.get("eventType") or "")
        event_type_upper = event_type.upper()
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
            return []

        created_jobs: list[str] = []
        change_desc = event_data.get("changeDescription") or {}
        tag_changed = self._has_tag_change(change_desc)

        # Tag changes are a separate lifecycle from classification. The webhook
        # does not decide which tags are Confirmed; the execution worker reads
        # the live OpenMetadata entity and then reconciles Ranger from that state.
        if tag_changed:
            logical = json.dumps(
                {
                    "event_id": event_id or f"timestamp:{timestamp}",
                    "event_type": event_type_upper,
                    "entity_type": entity_type,
                    "entity_fqn": entity_fqn,
                    "purpose": "refresh-confirmed-tags",
                },
                sort_keys=True,
            )
            fingerprint = hashlib.sha256(logical.encode()).hexdigest()
            job = self.jobs.enqueue(
                job_type=JobType.RECONCILE_RANGER,
                idempotency_key=f"confirmed-tags-refresh:{fingerprint}",
                payload={
                    "entity_type": entity_type,
                    "entity_fqn": entity_fqn,
                    "refresh_confirmed_tags": True,
                    "classification_run_id": None,
                    "correlation_id": correlation_id,
                },
                correlation_id=correlation_id,
                max_attempts=5,
            )
            self.audit.record(
                actor_id="system:openmetadata-webhook",
                actor_name="OpenMetadata Webhook Adapter",
                action="CONFIRMED_TAG_REFRESH_ENQUEUED",
                object_type=entity_type,
                object_id=entity_fqn,
                correlation_id=correlation_id,
                details={
                    "event_id": event_id,
                    "event_type": event_type,
                    "next_job_id": str(job.id),
                    "snapshot_source": "openmetadata-readback",
                },
            )
            created_jobs.append(str(job.id))

        # Do not re-run classification for a pure tag lifecycle event. Accepting
        # a native Suggestion changes tags and must not create the same Suggestion
        # again. Metadata creation and non-tag metadata updates still classify.
        should_classify = event_type_upper == "ENTITY_CREATED" or (
            event_type_upper in {"ENTITY_UPDATED", "ENTITY_FIELDS_CHANGED"}
            and not tag_changed
        )
        if should_classify:
            fields = self._extract_fields(event_data)
            entity_name = str(
                event_data.get("entity", {}).get("name")
                or entity_fqn.split(".")[-1]
            )
            description = event_data.get("entity", {}).get("description")

            normalized = MetadataEventRequest(
                event_id=event_id or f"evt-{timestamp}",
                event_type=(
                    "ENTITY_CREATED"
                    if event_type_upper == "ENTITY_CREATED"
                    else "ENTITY_UPDATED"
                ),
                entity_type=entity_type,
                entity_fqn=entity_fqn,
                entity_name=entity_name,
                description=description,
                fields=fields,
                existing_tags=[],
                correlation_id=correlation_id,
            )

            logical_classify = (
                f"{normalized.event_id}|{normalized.entity_type}|{normalized.entity_fqn}"
            )
            fingerprint_classify = hashlib.sha256(logical_classify.encode()).hexdigest()
            job = self.jobs.enqueue(
                job_type=JobType.CLASSIFY_ASSET,
                idempotency_key=f"classify-webhook:{fingerprint_classify}",
                payload=normalized.model_dump(mode="json"),
                correlation_id=correlation_id,
                max_attempts=3,
            )
            self.audit.record(
                actor_id="system:openmetadata-webhook",
                actor_name="OpenMetadata Webhook Adapter",
                action="ASSET_CLASSIFICATION_ENQUEUED",
                object_type=entity_type,
                object_id=entity_fqn,
                correlation_id=correlation_id,
                details={
                    "event_id": event_id,
                    "event_type": event_type,
                    "next_job_id": str(job.id),
                },
            )
            created_jobs.append(str(job.id))

        return created_jobs

    @classmethod
    def _has_tag_change(cls, change_desc: dict[str, Any]) -> bool:
        """Return True when ChangeDescription contains a tag lifecycle signal.

        OpenMetadata can represent column tag changes at different nesting
        levels. We therefore inspect field names and old/new payloads instead of
        requiring an exact ``name == 'tags'`` shape.
        """

        for bucket in ("fieldsAdded", "fieldsUpdated", "fieldsDeleted"):
            changes = change_desc.get(bucket, []) or []
            for change in changes:
                if not isinstance(change, dict):
                    continue

                name = str(change.get("name") or "").lower()
                if "tag" in name:
                    return True

                if cls._contains_tag_payload(change.get("oldValue")):
                    return True
                if cls._contains_tag_payload(change.get("newValue")):
                    return True
        return False

    @classmethod
    def _contains_tag_payload(cls, value: Any) -> bool:
        if isinstance(value, dict):
            for key, nested in value.items():
                if "tag" in str(key).lower():
                    return True
                if cls._contains_tag_payload(nested):
                    return True
            return False

        if isinstance(value, list):
            return any(cls._contains_tag_payload(item) for item in value)

        if isinstance(value, str):
            lowered = value.lower()
            return "tagfqn" in lowered or '"tags"' in lowered or "taglabels" in lowered

        return False

    def _extract_fields(self, event_data: dict[str, Any]) -> list[MetadataField]:
        entity = event_data.get("entity") or {}
        fields: list[MetadataField] = []
        for column in entity.get("columns", []) or []:
            if not isinstance(column, dict) or not column.get("name"):
                continue
            fields.append(
                MetadataField(
                    name=column["name"],
                    data_type=column.get("dataType"),
                    description=column.get("description"),
                    sample_values=[],
                )
            )
        return fields
