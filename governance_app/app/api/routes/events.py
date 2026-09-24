from fastapi import APIRouter, status

from app.api.dependencies import AppSettings, DbSession
from app.schemas.common import AcceptedResponse
from app.schemas.events import ConfirmedTagEventRequest
from app.services.intake import IntakeService

router = APIRouter()


@router.post(
    "/confirmed-tags",
    response_model=AcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def accept_confirmed_tag_event(
    request: ConfirmedTagEventRequest,
    db: DbSession,
    settings: AppSettings,
) -> AcceptedResponse:
    """Trigger Ranger convergence from OpenMetadata's confirmed tag state."""
    with db.begin():
        job = IntakeService(db, settings).accept_confirmed_tag_event(request)
    return AcceptedResponse(job_id=job.id, status=job.status)
