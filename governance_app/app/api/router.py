from fastapi import APIRouter

from app.api.routes import (
    capabilities,
    data_access_policies,
    dq,
    events,
    health,
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
# R4 authoritative logical policy API.
api_router.include_router(
    data_access_policies.router,
    prefix="/data-access-policies",
    tags=["data-access-policies"],
)
api_router.include_router(
    dq.router,
    prefix="/dq",
    tags=["dq"],
)
