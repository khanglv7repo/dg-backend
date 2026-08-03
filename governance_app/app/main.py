from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.clients.ranger import RangerClient
from app.clients.ranger_tags import RangerTagStoreClient
from app.core.config import get_settings
from app.core.errors import (
    AuthorizationError,
    ConflictError,
    ExternalSystemError,
    GovernanceError,
    NotFoundError,
    ValidationError,
)
from app.core.logging import configure_logging
from app.models.enums import JobType
from app.services.policy_sync import RangerTagPolicyCatalogService
from app.workers.base import Worker

settings = get_settings()
configure_logging(settings.app_log_level)
logger = logging.getLogger(__name__)


def _ranger_secret() -> str | None:
    if settings.ranger_service_secret is None:
        return None
    return settings.ranger_service_secret.get_secret_value()


def _reconcile_ranger_tag_policy_catalog() -> dict:
    policy_client = RangerClient(
        base_url=settings.ranger_base_url,
        username=settings.ranger_service_account,
        password=_ranger_secret(),
        service_name=settings.ranger_tag_service_name,
        dry_run=settings.ranger_dry_run,
        timeout=settings.ranger_timeout_seconds,
    )
    tag_store = RangerTagStoreClient(
        base_url=settings.ranger_tag_store_base_url,
        username=settings.ranger_service_account,
        password=_ranger_secret(),
        resource_service_name=settings.ranger_service_name,
        dry_run=settings.ranger_dry_run,
        timeout=settings.ranger_timeout_seconds,
    )
    try:
        return RangerTagPolicyCatalogService(
            settings,
            policy_client,
            tag_store,
        ).reconcile()
    finally:
        tag_store.close()
        policy_client.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    worker = None
    worker_thread = None

    # Flow A. This does not read OpenMetadata and does not wait for any asset.
    # With RANGER_DRY_RUN=false, a backend restart makes config/policies.yaml
    # converge into the Ranger tag service before the execution worker starts.
    if (
        settings.ranger_enabled
        and settings.ranger_reconcile_tag_policies_on_startup
        and settings.app_env != "test"
    ):
        result = _reconcile_ranger_tag_policy_catalog()
        app.state.ranger_tag_policy_catalog = result
        logger.info(
            "Ranger tag-policy startup reconcile complete: "
            "tag_service=%s policies=%s dry_run=%s",
            result["tag_service"],
            result["policies"],
            result["dry_run"],
        )

    if settings.auto_start_execution_worker and settings.app_env != "test":
        worker = Worker(
            role="execution",
            worker_id=settings.execution_worker_id,
            excluded_job_types={JobType.AGENT_CLASSIFY},
            settings=settings,
        )
        worker_thread = threading.Thread(
            target=worker.run,
            daemon=True,
        )
        worker_thread.start()

    yield

    if worker:
        worker.stop()
        if worker_thread:
            worker_thread.join(timeout=2.0)


app = FastAPI(
    title="OpenMetadata-Native Governance Platform",
    version="0.5.0",
    description=(
        "FastAPI governance control plane with independent Ranger tag-policy "
        "and OpenMetadata tag-assignment flows."
    ),
    lifespan=lifespan,
)
app.include_router(api_router, prefix=settings.api_prefix)


@app.exception_handler(GovernanceError)
async def governance_error_handler(
    _request: Request,
    exc: GovernanceError,
) -> JSONResponse:
    status_code = 400
    if isinstance(exc, NotFoundError):
        status_code = 404
    elif isinstance(exc, ConflictError):
        status_code = 409
    elif isinstance(exc, AuthorizationError):
        status_code = 403
    elif isinstance(exc, ValidationError):
        status_code = 422
    elif isinstance(exc, ExternalSystemError):
        status_code = 503 if exc.retryable else 502
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            }
        },
    )
