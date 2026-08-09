from fastapi import APIRouter

from app.api.routes import (
    capabilities,
    classification_rules,
    classification_runs,
    classifications,
    data_access_policies,
    events,
    health,
    jobs,
    openmetadata_events,
    policies,
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
    classifications.router,
    prefix="/classifications",
    tags=["classifications"],
)
api_router.include_router(
    classification_rules.router,
    prefix="/classification-rules",
    tags=["classification-rules"],
)
api_router.include_router(
    classification_runs.router,
    prefix="/classification-runs",
    tags=["classification-runs"],
)
# R4 authoritative logical policy API. The legacy /policies native-Ranger JSON
# catalog remains below for compatibility but is not an R4 source of truth.
api_router.include_router(
    data_access_policies.router,
    prefix="/data-access-policies",
    tags=["data-access-policies"],
)
api_router.include_router(
    policies.router,
    prefix="/policies",
    tags=["policies"],
    deprecated=True,
)
api_router.include_router(
    jobs.router,
    prefix="/jobs",
    tags=["jobs"],
)
