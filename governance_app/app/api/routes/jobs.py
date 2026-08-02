from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from app.api.dependencies import CurrentActor, DbSession
from app.core.errors import AuthorizationError
from app.repositories.audit import AuditRepository
from app.repositories.jobs import JobRepository
from app.schemas.jobs import JobResponse, JobRetryRequest

router = APIRouter()


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: UUID, db: DbSession) -> JobResponse:
    return JobResponse.model_validate(JobRepository(db).get(job_id))


@router.post("/{job_id}/retry", response_model=JobResponse)
def retry_job(
    job_id: UUID,
    request: JobRetryRequest,
    db: DbSession,
    actor: CurrentActor,
) -> JobResponse:
    if not actor.has_any_role("governance-operator", "governance-admin"):
        raise AuthorizationError("governance-operator or governance-admin role is required")
    with db.begin():
        job = JobRepository(db).retry(job_id)
        AuditRepository(db).record(
            actor_id=actor.subject,
            actor_name=actor.display_name,
            action="JOB_MANUALLY_RETRIED",
            object_type="job",
            object_id=str(job.id),
            correlation_id=job.correlation_id,
            details={"reason": request.reason},
        )
    return JobResponse.model_validate(job)
