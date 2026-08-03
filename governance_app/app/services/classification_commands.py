from __future__ import annotations

import hashlib
import uuid

from sqlalchemy.orm import Session

from app.clients.openmetadata import OpenMetadataClient
from app.core.config import Settings
from app.core.errors import ConfigurationError
from app.models.enums import JobType
from app.repositories.audit import AuditRepository
from app.repositories.jobs import JobRepository
from app.schemas.events import MetadataEventRequest, MetadataField
from app.services.classification import ClassificationService


class ClassificationCommandService:
    """Manual command boundary for running deterministic classification from OM."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def enqueue_asset(
        self,
        *,
        entity_type: str,
        entity_fqn: str,
        correlation_id: str | None,
    ):
        if not self.settings.openmetadata_enabled:
            raise ConfigurationError("OpenMetadata integration is disabled")
        event_id = f"manual-classification:{uuid.uuid4()}"
        fingerprint = hashlib.sha256(event_id.encode()).hexdigest()
        job = JobRepository(self.session).enqueue(
            job_type=JobType.CLASSIFY_ASSET_FROM_OM,
            idempotency_key=f"classify-from-om:{fingerprint}",
            payload={
                "event_id": event_id,
                "entity_type": entity_type,
                "entity_fqn": entity_fqn,
                "correlation_id": correlation_id,
            },
            correlation_id=correlation_id,
            max_attempts=3,
        )
        AuditRepository(self.session).record(
            actor_id="system:classification-command",
            actor_name="Classification Command",
            action="CLASSIFICATION_COMMAND_ACCEPTED",
            object_type=entity_type,
            object_id=entity_fqn,
            correlation_id=correlation_id,
            details={"job_id": str(job.id)},
        )
        return job


class OpenMetadataClassificationRunner:
    """Hydrate the current OM asset and run the existing rule engine."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        client: OpenMetadataClient,
    ) -> None:
        self.session = session
        self.settings = settings
        self.client = client

    def run(self, payload: dict) -> dict:
        entity_type = str(payload.get("entity_type") or "table")
        entity_fqn = str(payload["entity_fqn"])
        entity = self.client.get_entity(
            entity_type=entity_type,
            fqn=entity_fqn,
            fields="tags,columns,description",
        )

        fields: list[MetadataField] = []
        for column in entity.get("columns", []) or []:
            if not isinstance(column, dict):
                continue
            name = str(column.get("name") or "").strip()
            if not name:
                continue
            data_type = column.get("dataTypeDisplay") or column.get("dataType")
            fields.append(
                MetadataField(
                    name=name,
                    data_type=str(data_type) if data_type is not None else None,
                    description=(
                        str(column.get("description"))
                        if column.get("description") is not None
                        else None
                    ),
                )
            )

        existing_tags = sorted(
            {
                str(item.get("tagFQN"))
                for item in entity.get("tags", []) or []
                if isinstance(item, dict) and item.get("tagFQN")
            }
        )
        event = MetadataEventRequest(
            event_id=str(payload["event_id"]),
            event_type="MANUAL_CLASSIFICATION",
            entity_type=entity_type,
            entity_fqn=entity_fqn,
            entity_name=str(entity.get("name") or entity_fqn.rsplit(".", 1)[-1]),
            description=(
                str(entity.get("description"))
                if entity.get("description") is not None
                else None
            ),
            fields=fields,
            existing_tags=existing_tags,
            raw_event={},
            correlation_id=payload.get("correlation_id"),
        )
        return ClassificationService(self.session, self.settings).classify(event)
