from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.repositories.audit import AuditRepository
from app.schemas.events import (
    ConfirmedTagEventRequest,
    MetadataEventRequest,
)
from app.services.classification_rule_catalog import (
    ClassificationRuleCatalogService,
)


class _DispatchedTaskRef:
    """Minimal shim exposing (id, status) matching the legacy
    GovernanceJob-returning contract that app/api/routes/events.py's
    AcceptedResponse still expects, backed by a real Celery AsyncResult
    (docs/13_IMPLEMENTATION_SPEC.md section 9 job engine decision).
    """

    def __init__(self, task) -> None:
        self._task = task
        # Celery task ids are UUID-format strings; the API's response model
        # requires a real uuid.UUID -- always true in practice, but guard
        # rather than assume.
        self.id = uuid.UUID(str(task.id)) if getattr(task, "id", None) else uuid.uuid4()

    @property
    def status(self) -> str:
        try:
            return str(self._task.status)
        except Exception:
            return "QUEUED"


class IntakeService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
    ) -> None:
        self.session = session
        self.settings = settings

    def accept_metadata_event(
        self,
        request: MetadataEventRequest,
    ):
        # Bind idempotency to the ACTIVE DB-backed rule version.
        engine = ClassificationRuleCatalogService(
            self.session
        ).active_engine()

        logical = (
            f"{request.event_id}|"
            f"{request.entity_type}|"
            f"{request.entity_fqn}|"
            f"{engine.configuration_version}"
        )
        # `fingerprint` retained for audit correlation; Celery dispatch
        # replaces the legacy JobRepository/GovernanceJob queue
        # (docs/13_IMPLEMENTATION_SPEC.md section 9 job engine decision).
        fingerprint = hashlib.sha256(
            logical.encode()
        ).hexdigest()

        from app.tasks.classification import classify_asset

        task = classify_asset.delay(payload=request.model_dump(mode="json"))
        job = _DispatchedTaskRef(task)

        AuditRepository(
            self.session
        ).record(
            actor_id="system:intake",
            actor_name="Metadata Intake",
            action="METADATA_EVENT_ACCEPTED",
            object_type="job",
            object_id=str(job.id),
            correlation_id=(
                request.correlation_id
            ),
            details={
                "event_id":
                    request.event_id,
                "entity_fqn":
                    request.entity_fqn,
                "classification_rule_version":
                    engine.configuration_version,
                "classification_rule_sha256":
                    engine.configuration_sha256,
                "idempotency_fingerprint":
                    fingerprint,
            },
        )
        return job

    def accept_confirmed_tag_event(
        self,
        request: ConfirmedTagEventRequest,
    ):
        """Compatibility intake for a normalized Confirmed-tag trigger.

        Caller-supplied tags are not trusted. The worker reads live
        OpenMetadata state and syncs only tag assignments into Ranger's tag
        store.
        """

        logical = json.dumps(
            {
                "event_id":
                    request.event_id,
                "entity_type":
                    request.entity_type,
                "entity_fqn":
                    request.entity_fqn,
                "source":
                    request.source,
                "purpose":
                    "sync-ranger-tag-assignments",
            },
            sort_keys=True,
        )
        fingerprint = hashlib.sha256(
            logical.encode()
        ).hexdigest()

        from app.tasks.tag_sync import sync_tags_to_ranger

        task = sync_tags_to_ranger.delay(
            entity_type=request.entity_type,
            entity_fqn=request.entity_fqn,
            correlation_id=request.correlation_id,
        )
        job = _DispatchedTaskRef(task)

        AuditRepository(
            self.session
        ).record(
            actor_id="system:openmetadata",
            actor_name="OpenMetadata",
            action="CONFIRMED_TAG_EVENT_ACCEPTED",
            object_type=request.entity_type,
            object_id=request.entity_fqn,
            correlation_id=(
                request.correlation_id
            ),
            details={
                "event_id":
                    request.event_id,
                "source":
                    request.source,
                "next_job_id":
                    str(job.id),
                "snapshot_source":
                    "openmetadata-readback",
            },
        )
        return job
