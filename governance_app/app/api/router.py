from fastapi import APIRouter

from app.api.routes import (
    capabilities,
    classification_runs,
    classifications,
    events,
    health,
    jobs,
    openmetadata_events,
    policies,
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
    classifications.router,
    prefix="/classifications",
    tags=["classifications"],
)
api_router.include_router(
    classification_runs.router,
    prefix="/classification-runs",
    tags=["classification-runs"],
)
api_router.include_router(policies.router, prefix="/policies", tags=["policies"])
api_router.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
