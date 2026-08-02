from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.errors import GovernanceError
from app.jobs.dispatcher import JobDispatcher
from app.models.enums import JobStatus
from app.repositories.audit import AuditRepository
from app.repositories.jobs import JobRepository

logger = logging.getLogger(__name__)


class JobProcessor:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        settings: Settings,
        worker_id: str,
        worker_role: str,
        dispatcher: JobDispatcher | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.worker_id = worker_id
        self.worker_role = worker_role
        self.dispatcher = dispatcher or JobDispatcher()

    def process(self, job_id: uuid.UUID) -> None:
        try:
            with self.session_factory() as session, session.begin():
                repository = JobRepository(session)
                job = repository.get(job_id)
                if job.status != JobStatus.RUNNING.value:
                    return
                result = self.dispatcher.dispatch(session, self.settings, job)
                repository.mark_succeeded(job)
                AuditRepository(session).record(
                    actor_id=f"service:{self.worker_id}",
                    actor_name=self.worker_id,
                    action="JOB_SUCCEEDED",
                    object_type="job",
                    object_id=str(job.id),
                    correlation_id=job.correlation_id,
                    details={
                        "job_type": job.job_type,
                        "worker_role": self.worker_role,
                        "result": result,
                    },
                )
        except GovernanceError as exc:
            self._persist_failure(job_id, exc.code, exc.message, exc.retryable)
        except Exception as exc:
            logger.exception("unhandled job exception", extra={"job_id": str(job_id)})
            self._persist_failure(job_id, "UNHANDLED_EXCEPTION", str(exc), False)

    def _persist_failure(self, job_id: uuid.UUID, code: str, message: str, retryable: bool) -> None:
        with self.session_factory() as session, session.begin():
            repository = JobRepository(session)
            job = repository.get(job_id)
            repository.mark_failed(job, code=code, message=message, retryable=retryable)
            AuditRepository(session).record(
                actor_id=f"service:{self.worker_id}",
                actor_name=self.worker_id,
                action="JOB_FAILED" if job.status == JobStatus.DEAD.value else "JOB_RETRY_SCHEDULED",
                object_type="job",
                object_id=str(job.id),
                correlation_id=job.correlation_id,
                details={
                    "job_type": job.job_type,
                    "worker_role": self.worker_role,
                    "error_code": code,
                    "error_message": message[:1000],
                    "attempt_count": job.attempt_count,
                    "status": job.status,
                },
            )
