
from fastapi import APIRouter

from app.api.routes import (
    capabilities,
    classification_runs,
    events,
    health,
    jobs,
    openmetadata_events,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(capabilities.router, prefix="/capabilities", tags=["capabilities"])
api_router.include_router(events.router, prefix="/events", tags=["events"])
api_router.include_router(
    openmetadata_events.router,
    prefix="/integrations/openmetadata",
    tags=["openmetadata-integrations"],
)
api_router.include_router(
    classification_runs.router, prefix="/classification-runs", tags=["classification-runs"]
)
api_router.include_router(jobs.router, prefix="/jobs", tags=["jobs"])

