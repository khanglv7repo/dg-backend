from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.repositories.audit import AuditRepository
from app.schemas.events import ConfirmedTagEventRequest


class _DispatchedTaskRef:
    """Minimal AsyncResult compatibility wrapper for AcceptedResponse."""

    def __init__(self, task) -> None:
        self._task = task
        self.id = uuid.UUID(str(task.id)) if getattr(task, "id", None) else uuid.uuid4()

    @property
    def status(self) -> str:
        try:
            return str(self._task.status)
        except Exception:
            return "QUEUED"


class IntakeService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def accept_confirmed_tag_event(self, request: ConfirmedTagEventRequest):
        """Trigger Ranger tag convergence from live OpenMetadata state.

        Caller-supplied tags are never authoritative. The worker re-reads
        OpenMetadata and propagates only confirmed assignments.
        """
        logical = json.dumps(
            {
                "event_id": request.event_id,
                "entity_type": request.entity_type,
                "entity_fqn": request.entity_fqn,
                "source": request.source,
                "purpose": "sync-ranger-tag-assignments",
            },
            sort_keys=True,
        )
        fingerprint = hashlib.sha256(logical.encode()).hexdigest()

        from app.tasks.tag_sync import sync_tags_to_ranger

        task = sync_tags_to_ranger.delay(
            entity_type=request.entity_type,
            entity_fqn=request.entity_fqn,
            correlation_id=request.correlation_id,
        )
        job = _DispatchedTaskRef(task)

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
                "idempotency_fingerprint": fingerprint,
            },
        )
        return job
