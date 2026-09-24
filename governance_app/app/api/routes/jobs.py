from __future__ import annotations

import logging
from uuid import UUID

from celery.result import AsyncResult
from fastapi import APIRouter

from app.api.dependencies import CurrentActor, DbSession
from app.celery_app import app as celery_app
from app.core.errors import AuthorizationError, NotFoundError
from app.repositories.audit import AuditRepository
from app.repositories.jobs import JobRepository
from app.schemas.jobs import JobResponse, JobRetryRequest, CeleryTaskStatusResponse

router = APIRouter()
logger = logging.getLogger(__name__)


def _celery_status_response(task_id: str) -> CeleryTaskStatusResponse:
    """Build a CeleryTaskStatusResponse from an AsyncResult.

    Celery task states: PENDING, STARTED, RETRY, SUCCESS, FAILURE.
    Normalised to the same vocabulary callers already expect from the legacy
    GovernanceJob API where practical.
    """
    result = AsyncResult(str(task_id), app=celery_app)
    return CeleryTaskStatusResponse(
        id=UUID(str(task_id)),
        source="celery_task",
        status=result.state,
        info=str(result.info) if result.info and result.state not in {"SUCCESS"} else None,
    )


@router.get("/{job_id}")
def get_job(job_id: UUID, db: DbSession) -> JobResponse | CeleryTaskStatusResponse:
    """Return status for a job by ID.

    Tries the legacy GovernanceJob table first (for historical rows created
    before the Celery migration), then falls back to Celery AsyncResult for
    task IDs dispatched via .delay() after the migration
    (docs/13_IMPLEMENTATION_SPEC.md section 9 — parallel Celery-result-backed
    status/retry API alongside the legacy GovernanceJob path).
    """
    try:
        return JobResponse.model_validate(JobRepository(db).get(job_id))
    except NotFoundError:
        pass

    # Fall back to Celery AsyncResult for post-migration task IDs.
    # PENDING is Celery's default for unknown IDs — if the task was never
    # submitted with this UUID the caller gets a PENDING response, which is
    # indistinguishable from "just submitted, not yet started". This is a
    # known Celery limitation; callers should use a durable dispatch record
    # (Outbox / audit log) to confirm a task was actually submitted.
    return _celery_status_response(str(job_id))


@router.post("/{job_id}/retry", response_model=JobResponse)
def retry_job(
    job_id: UUID,
    request: JobRetryRequest,
    db: DbSession,
    actor: CurrentActor,
) -> JobResponse:
    """Retry a legacy GovernanceJob row.

    For tasks dispatched via Celery .delay() (post-migration), retry handling
    is internal to each task (task_acks_late=True, max_retries=N configured per
    task). This route only supports retrying legacy GovernanceJob DB rows.
    Celery task IDs will return 404 (use the GET endpoint to poll status).
    """
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

