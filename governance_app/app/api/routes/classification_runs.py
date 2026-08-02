
from uuid import UUID

from fastapi import APIRouter

from app.api.dependencies import DbSession
from app.repositories.classification import ClassificationRunRepository
from app.schemas.classification_runs import ClassificationRunResponse

router = APIRouter()


@router.get("/{run_id}", response_model=ClassificationRunResponse)
def get_classification_run(run_id: UUID, db: DbSession) -> ClassificationRunResponse:
    return ClassificationRunResponse.model_validate(ClassificationRunRepository(db).get(run_id))
