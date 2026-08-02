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
from app.schemas.events import ConfirmedTagEventRequest, MetadataEventRequest, MetadataField

logger = logging.getLogger(__name__)


class OpenMetadataEventAdapterService:
    """Adapter for raw OpenMetadata ChangeEvent webhooks.

    Deduplicates events, validates authorization, parses entity and tag changes,
    and enqueues CLASSIFY_ASSET or RECONCILE_RANGER jobs accordingly.
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
        """Process a raw OpenMetadata ChangeEvent dictionary."""
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
            return []

        created_jobs: list[str] = []

        # 1. Detect Tag Changes (Confirmed tags added or removed)
        change_desc = event_data.get("changeDescription") or {}
        tag_changed, tags, field_paths = self._extract_confirmed_tags(event_data, change_desc)

        if tag_changed:
            resolver_version = "v1"
            logical = json.dumps(
                {
                    "event_id": event_id,
                    "entity_fqn": entity_fqn,
                    "tags": sorted(set(tags)),
                    "field_paths": field_paths,
                    "version": resolver_version,
                },
                sort_keys=True,
            )
            fingerprint = hashlib.sha256(logical.encode()).hexdigest()
            job = self.jobs.enqueue(
                job_type=JobType.RECONCILE_RANGER,
                idempotency_key=f"confirmed-tags-event:{fingerprint}",
                payload={
                    "entity_fqn": entity_fqn,
                    "tags": sorted(set(tags)),
                    "field_paths": field_paths,
                    "classification_run_id": None,
                    "correlation_id": correlation_id,
                },
                correlation_id=correlation_id,
            )
            self.audit.record(
                actor_id="system:openmetadata-webhook",
                actor_name="OpenMetadata Webhook Adapter",
                action="CONFIRMED_TAG_CHANGE_DETECTED",
                object_type=entity_type,
                object_id=entity_fqn,
                correlation_id=correlation_id,
                details={
                    "event_id": event_id,
                    "event_type": event_type,
                    "tags": tags,
                    "field_paths": field_paths,
                },
            )
            created_jobs.append(str(job.id))

        # 2. Detect Entity / Schema Creation or Update -> Enqueue CLASSIFY_ASSET
        if event_type.upper() in {"ENTITY_CREATED", "ENTITY_UPDATED", "ENTITY_FIELDS_CHANGED"}:
            # Check if this change is an asset creation or relevant metadata update
            fields = self._extract_fields(event_data)
            entity_name = str(
                event_data.get("entity", {}).get("name")
                or entity_fqn.split(".")[-1]
            )
            description = event_data.get("entity", {}).get("description")

            norm_req = MetadataEventRequest(
                event_id=event_id or f"evt-{timestamp}",
                event_type="ENTITY_CREATED" if event_type == "ENTITY_CREATED" else "ENTITY_UPDATED",
                entity_type=entity_type,
                entity_fqn=entity_fqn,
                entity_name=entity_name,
                description=description,
                fields=fields,
                existing_tags=tags if tag_changed else [],
                correlation_id=correlation_id,
            )

            logical_classify = f"{norm_req.event_id}|{norm_req.entity_type}|{norm_req.entity_fqn}"
            fingerprint_classify = hashlib.sha256(logical_classify.encode()).hexdigest()
            job_c = self.jobs.enqueue(
                job_type=JobType.CLASSIFY_ASSET,
                idempotency_key=f"classify-webhook:{fingerprint_classify}",
                payload=norm_req.model_dump(mode="json"),
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
                details={"event_id": event_id, "event_type": event_type},
            )
            created_jobs.append(str(job_c.id))

        return created_jobs

    def _extract_confirmed_tags(
        self, event_data: dict[str, Any], change_desc: dict[str, Any]
    ) -> tuple[bool, list[str], dict[str, list[str]]]:
        entity = event_data.get("entity") or {}
        tags: list[str] = []
        field_paths: dict[str, list[str]] = {}
        tag_changed = False

        # Check fieldsAdded, fieldsUpdated, fieldsDeleted in changeDescription
        all_changes = (
            change_desc.get("fieldsAdded", [])
            + change_desc.get("fieldsUpdated", [])
            + change_desc.get("fieldsDeleted", [])
        )
        for change in all_changes:
            name = str(change.get("name") or "")
            if "tags" in name.lower() or "tags" in str(change.get("newValue") or ""):
                tag_changed = True

        # Read current entity confirmed tags
        if isinstance(entity, dict):
            # Entity tags
            for t in entity.get("tags", []):
                if isinstance(t, dict) and t.get("state", "Confirmed") == "Confirmed":
                    fqn = t.get("tagFQN")
                    if fqn:
                        tags.append(fqn)

            # Column tags
            for col in entity.get("columns", []):
                if isinstance(col, dict):
                    col_name = col.get("name")
                    if col_name:
                        cpath = f"columns.{col_name}"
                        for ct in col.get("tags", []):
                            if isinstance(ct, dict) and ct.get("state", "Confirmed") == "Confirmed":
                                cfqn = ct.get("tagFQN")
                                if cfqn:
                                    tags.append(cfqn)
                                    field_paths.setdefault(cfqn, []).append(cpath)

        return tag_changed, sorted(set(tags)), field_paths

    def _extract_fields(self, event_data: dict[str, Any]) -> list[MetadataField]:
        entity = event_data.get("entity") or {}
        fields: list[MetadataField] = []
        for col in entity.get("columns", []):
            if isinstance(col, dict) and col.get("name"):
                fields.append(
                    MetadataField(
                        name=col["name"],
                        data_type=col.get("dataType"),
                        description=col.get("description"),
                        sample_values=[],
                    )
                )
        return fields
