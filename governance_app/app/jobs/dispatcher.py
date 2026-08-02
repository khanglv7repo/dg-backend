from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ConfigurationError
from app.jobs.handlers import HANDLERS
from app.models.enums import JobType
from app.models.job import GovernanceJob


class JobDispatcher:
    def dispatch(self, session: Session, settings: Settings, job: GovernanceJob) -> dict:
        try:
            job_type = JobType(job.job_type)
        except ValueError as exc:
            raise ConfigurationError(f"unknown job type: {job.job_type}") from exc
        handler = HANDLERS.get(job_type)
        if not handler:
            raise ConfigurationError(f"no handler registered for job type: {job_type.value}")
        return handler(session, settings, dict(job.payload))
