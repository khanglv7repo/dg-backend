"""Read-only access to historical GovernanceJob rows.

GovernanceJob is no longer a runtime queue. Current asynchronous execution is
owned by Celery plus durable Inbox/Outbox/reconciliation state. This repository
exists only so old job IDs remain inspectable during migration.
"""
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.models.job import GovernanceJob


class JobRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, job_id: uuid.UUID | str) -> GovernanceJob:
        identifier = uuid.UUID(str(job_id))
        job = self.session.get(GovernanceJob, identifier)
        if not job:
            raise NotFoundError(f"job {identifier} was not found")
        return job
