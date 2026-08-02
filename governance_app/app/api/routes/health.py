from datetime import UTC, datetime

from fastapi import APIRouter
from sqlalchemy import text

from app.api.dependencies import DbSession
from app.schemas.common import HealthResponse

router = APIRouter()


@router.get("/health/live", response_model=HealthResponse)
def live() -> HealthResponse:
    return HealthResponse(status="ok", timestamp=datetime.now(UTC))


@router.get("/health/ready", response_model=HealthResponse)
def ready(db: DbSession) -> HealthResponse:
    db.execute(text("SELECT 1"))
    return HealthResponse(status="ready", timestamp=datetime.now(UTC))
