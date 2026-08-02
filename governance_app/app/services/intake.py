from __future__ import annotations

import hashlib
import json

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.enums import JobType
from app.repositories.audit import AuditRepository
from app.repositories.jobs import JobRepository
from app.rules.classification import ClassificationRuleEngine
from app.rules.policy_mapping import PolicyMappingResolver
from app.schemas.events import ConfirmedTagEventRequest, MetadataEventRequest


class IntakeService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def accept_metadata_event(self, request: MetadataEventRequest):
        engine = ClassificationRuleEngine.from_path(
            self.settings.resolve_path(self.settings.classification_rules_path)
        )
        logical = (
            f"{request.event_id}|{request.entity_type}|{request.entity_fqn}|"
            f"{engine.configuration_version}"
        )
        fingerprint = hashlib.sha256(logical.encode()).hexdigest()
        job = JobRepository(self.session).enqueue(
            job_type=JobType.CLASSIFY_ASSET,
            idempotency_key=f"classify:{fingerprint}",
            payload=request.model_dump(mode="json"),
            correlation_id=request.correlation_id,
            max_attempts=3,
        )
        AuditRepository(self.session).record(
            actor_id="system:intake",
            actor_name="Metadata Intake",
            action="METADATA_EVENT_ACCEPTED",
            object_type="job",
            object_id=str(job.id),
            correlation_id=request.correlation_id,
            details={
                "event_id": request.event_id,
                "entity_fqn": request.entity_fqn,
            },
        )
        return job

    def accept_confirmed_tag_event(self, request: ConfirmedTagEventRequest):
        """Enqueue Ranger reconciliation from live OpenMetadata state.

        ``tags`` and ``field_paths`` remain in the request model for backwards
        compatibility, but enforcement does not trust caller-provided tag state.
        The worker reads the current Confirmed tags from OpenMetadata before
        resolving ``config/policies.yaml``.
        """

        resolver = PolicyMappingResolver.from_path(
            self.settings.resolve_path(self.settings.policy_mappings_path)
        )
        logical = json.dumps(
            {
                "event_id": request.event_id,
                "entity_type": request.entity_type,
                "entity_fqn": request.entity_fqn,
                "source": request.source,
                "policy_version": resolver.configuration_version,
                "purpose": "refresh-confirmed-tags",
            },
            sort_keys=True,
        )
        fingerprint = hashlib.sha256(logical.encode()).hexdigest()
        job = JobRepository(self.session).enqueue(
            job_type=JobType.RECONCILE_RANGER,
            idempotency_key=f"confirmed-tags:{fingerprint}",
            payload={
                "entity_type": request.entity_type,
                "entity_fqn": request.entity_fqn,
                "refresh_confirmed_tags": True,
                "classification_run_id": None,
                "correlation_id": request.correlation_id,
            },
            correlation_id=request.correlation_id,
            max_attempts=5,
        )
        AuditRepository(self.session).record(
            actor_id="system:openmetadata",
            actor_name="OpenMetadata",
            action="CONFIRMED_TAG_EVENT_ACCEPTED",
            object_type=request.entity_type,
            object_id=request.entity_fqn,
            correlation_id=request.correlation_id,
            details={
                "event_id": request.event_id,
                "source": request.source,
                "next_job_id": str(job.id),
                "snapshot_source": "openmetadata-readback",
            },
        )
        return job
