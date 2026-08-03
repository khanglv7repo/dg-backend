from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.router import api_router
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
from app.db.session import SessionLocal
from app.models.enums import JobType
from app.services.policy_catalog import PolicyCatalogService
from app.services.policy_sync import PolicySyncCommandService
from app.workers.base import Worker

settings = get_settings()
configure_logging(settings.app_log_level)
logger = logging.getLogger(__name__)


def _prepare_policy_catalog() -> tuple[int, str]:
    """Seed legacy YAML once, then enqueue durable DB -> Ranger reconciliation."""

    with SessionLocal() as session, session.begin():
        seeded = PolicyCatalogService(session, settings).seed_legacy_catalog_if_empty()
        job = PolicySyncCommandService(session).enqueue(
            correlation_id="startup-policy-sync",
            actor_id="system:startup",
            actor_name="Application Startup",
        )
        return seeded, str(job.id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    worker = None
    worker_thread = None

    # Policy mutation is never performed inside the API lifespan. Startup only
    # seeds the DB catalog once (for migration compatibility) and creates a
    # durable execution-worker job.
    if (
        settings.ranger_enabled
        and settings.ranger_reconcile_tag_policies_on_startup
        and settings.app_env != "test"
    ):
        seeded, job_id = _prepare_policy_catalog()
        app.state.ranger_policy_sync_job_id = job_id
        logger.info(
            "Ranger policy sync queued: job_id=%s legacy_seeded=%s dry_run=%s",
            job_id,
            seeded,
            settings.ranger_dry_run,
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
    version="0.6.0",
    description=(
        "Governance control plane for OpenMetadata classification/tagging and "
        "PostgreSQL desired-state Ranger policy reconciliation."
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
