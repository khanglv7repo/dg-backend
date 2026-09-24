from fastapi import APIRouter

from app.api.routes import (
    capabilities,
    classification_runs,
    data_access_policies,
    dq,
    events,
    health,
    jobs,
    openmetadata_events,
)

api_router = APIRouter()

api_router.include_router(
    health.router,
    tags=["health"],
)
api_router.include_router(
    capabilities.router,
    prefix="/capabilities",
    tags=["capabilities"],
)
api_router.include_router(
    events.router,
    prefix="/events",
    tags=["events"],
)
api_router.include_router(
    openmetadata_events.router,
    prefix="/integrations/openmetadata",
    tags=["openmetadata-integrations"],
)
api_router.include_router(
    classification_runs.router,
    prefix="/classification-runs",
    tags=["classification-runs"],
)
# R4 authoritative logical policy API.
api_router.include_router(
    data_access_policies.router,
    prefix="/data-access-policies",
    tags=["data-access-policies"],
)
api_router.include_router(
    jobs.router,
    prefix="/jobs",
    tags=["jobs"],
)
api_router.include_router(
    dq.router,
    prefix="/dq",
    tags=["dq"],
)
