from fastapi import APIRouter, status

from app.api.dependencies import AppSettings, CurrentActor, DbSession
from app.core.errors import AuthorizationError
from app.schemas.classification_commands import ClassificationCommandRequest
from app.schemas.common import AcceptedResponse
from app.services.classification_commands import ClassificationCommandService

router = APIRouter()


@router.post(
    "/run",
    response_model=AcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def run_classification(
    request: ClassificationCommandRequest,
    db: DbSession,
    settings: AppSettings,
    actor: CurrentActor,
) -> AcceptedResponse:
    if not actor.has_any_role("governance-operator", "governance-admin"):
        raise AuthorizationError("governance-operator or governance-admin role is required")
    with db.begin():
        job = ClassificationCommandService(db, settings).enqueue_asset(
            entity_type=request.entity_type,
            entity_fqn=request.entity_fqn,
            correlation_id=request.correlation_id,
        )
    return AcceptedResponse(job_id=job.id, status=job.status)
