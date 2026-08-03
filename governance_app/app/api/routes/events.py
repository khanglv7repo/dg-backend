from fastapi import APIRouter, status

from app.api.dependencies import AppSettings, DbSession
from app.schemas.common import AcceptedResponse
from app.schemas.events import ConfirmedTagEventRequest, MetadataEventRequest
from app.services.intake import IntakeService

router = APIRouter()


@router.post("/metadata", response_model=AcceptedResponse, status_code=status.HTTP_202_ACCEPTED)
def accept_metadata_event(
    request: MetadataEventRequest,
    db: DbSession,
    settings: AppSettings,
) -> AcceptedResponse:
    """Accept an OpenMetadata event or manual classification trigger.

    The API only creates durable work. Execution happens through PostgreSQL-backed worker jobs.
    """
    with db.begin():
        job = IntakeService(db, settings).accept_metadata_event(request)
    return AcceptedResponse(job_id=job.id, status=job.status)


@router.post(
    "/confirmed-tags", response_model=AcceptedResponse, status_code=status.HTTP_202_ACCEPTED
)
def accept_confirmed_tag_event(
    request: ConfirmedTagEventRequest,
    db: DbSession,
    settings: AppSettings,
) -> AcceptedResponse:
    with db.begin():
        job = IntakeService(db, settings).accept_confirmed_tag_event(request)
    return AcceptedResponse(job_id=job.id, status=job.status)
