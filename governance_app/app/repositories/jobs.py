from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.models.enums import JobStatus, JobType
from app.models.job import GovernanceJob


class JobRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def enqueue(
        self,
        *,
        job_type: JobType,
        idempotency_key: str,
        payload: dict,
        correlation_id: str | None = None,
        priority: int = 100,
        max_attempts: int = 8,
    ) -> GovernanceJob:
        existing = self.session.scalar(
            select(GovernanceJob).where(GovernanceJob.idempotency_key == idempotency_key)
        )
        if existing:
            return existing
        job = GovernanceJob(
            job_type=job_type.value,
            status=JobStatus.QUEUED.value,
            priority=priority,
            idempotency_key=idempotency_key,
            payload=payload,
            correlation_id=correlation_id,
            max_attempts=max_attempts,
        )
        try:
            with self.session.begin_nested():
                self.session.add(job)
                self.session.flush()
            return job
        except IntegrityError:
            # A concurrent transaction won the same idempotency key.
            existing = self.session.scalar(
                select(GovernanceJob).where(
                    GovernanceJob.idempotency_key == idempotency_key
                )
            )
            if not existing:
                raise
            return existing

    def get(self, job_id: uuid.UUID | str) -> GovernanceJob:
        identifier = uuid.UUID(str(job_id))
        job = self.session.get(GovernanceJob, identifier)
        if not job:
            raise NotFoundError(f"job {identifier} was not found")
        return job

    def claim_batch(
        self,
        *,
        worker_id: str,
        limit: int,
        allowed_job_types: set[JobType] | None = None,
        excluded_job_types: set[JobType] | None = None,
    ) -> list[GovernanceJob]:
        now = datetime.now(UTC)
        conditions = [
            GovernanceJob.status.in_([JobStatus.QUEUED.value, JobStatus.RETRY_WAIT.value]),
            GovernanceJob.available_at <= now,
        ]
        if allowed_job_types:
            conditions.append(
                GovernanceJob.job_type.in_([item.value for item in sorted(allowed_job_types)])
            )
        if excluded_job_types:
            conditions.append(
                GovernanceJob.job_type.not_in([item.value for item in sorted(excluded_job_types)])
            )
        statement: Select = (
            select(GovernanceJob)
            .where(*conditions)
            .order_by(GovernanceJob.priority.asc(), GovernanceJob.created_at.asc())
            .limit(limit)
        )
        if self.session.bind and self.session.bind.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        jobs = list(self.session.scalars(statement))
        for job in jobs:
            job.status = JobStatus.RUNNING.value
            job.locked_by = worker_id
            job.locked_at = now
            job.heartbeat_at = now
            job.attempt_count += 1
        self.session.flush()
        return jobs

    def mark_succeeded(self, job: GovernanceJob) -> None:
        job.status = JobStatus.SUCCEEDED.value
        job.locked_by = None
        job.locked_at = None
        job.heartbeat_at = None
        job.last_error_code = None
        job.last_error_message = None

    def mark_failed(self, job: GovernanceJob, *, code: str, message: str, retryable: bool) -> None:
        bounded = message[:4000]
        job.last_error_code = code[:128]
        job.last_error_message = bounded
        job.locked_by = None
        job.locked_at = None
        job.heartbeat_at = None
        if retryable and job.attempt_count < job.max_attempts:
            job.status = JobStatus.RETRY_WAIT.value
            delay = min(300, 2 ** max(job.attempt_count, 1)) + random.uniform(0, 1)
            job.available_at = datetime.now(UTC) + timedelta(seconds=delay)
        else:
            job.status = JobStatus.DEAD.value

    def retry(self, job_id: uuid.UUID) -> GovernanceJob:
        job = self.get(job_id)
        if job.status not in {JobStatus.DEAD.value, JobStatus.CANCELLED.value}:
            raise ConflictError(f"job {job_id} cannot be retried from status {job.status}")
        job.status = JobStatus.QUEUED.value
        job.available_at = datetime.now(UTC)
        job.last_error_code = None
        job.last_error_message = None
        job.locked_by = None
        job.locked_at = None
        return job

    def recover_stale(self, *, stale_before: datetime) -> int:
        result = self.session.execute(
            update(GovernanceJob)
            .where(
                GovernanceJob.status == JobStatus.RUNNING.value,
                GovernanceJob.heartbeat_at < stale_before,
            )
            .values(
                status=JobStatus.QUEUED.value,
                locked_by=None,
                locked_at=None,
                heartbeat_at=None,
                available_at=datetime.now(UTC),
            )
        )
        return int(result.rowcount or 0)
