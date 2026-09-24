from __future__ import annotations

import logging
from uuid import UUID

from celery.result import AsyncResult
from fastapi import APIRouter

from app.api.dependencies import DbSession
from app.celery_app import app as celery_app
from app.core.errors import NotFoundError
from app.repositories.jobs import JobRepository
from app.schemas.jobs import JobResponse, CeleryTaskStatusResponse

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

    Reads legacy GovernanceJob rows for historical compatibility, then falls
    back to Celery AsyncResult for current task IDs. Legacy rows are read-only;
    no new GovernanceJob work is created or retried.
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


