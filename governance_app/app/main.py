from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import threading
from contextlib import asynccontextmanager

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
from app.models.enums import JobType
from app.workers.base import Worker

settings = get_settings()
configure_logging(settings.app_log_level)


@asynccontextmanager
async def lifespan(app: FastAPI):
    worker = None
    worker_thread = None
    if settings.auto_start_execution_worker and settings.app_env != "test":
        worker = Worker(
            role="execution",
            worker_id=settings.execution_worker_id,
            excluded_job_types={JobType.AGENT_CLASSIFY},
            settings=settings,
        )
        worker_thread = threading.Thread(target=worker.run, daemon=True)
        worker_thread.start()
    yield
    if worker:
        worker.stop()
        if worker_thread:
            worker_thread.join(timeout=2.0)


app = FastAPI(
    title="OpenMetadata-Native Governance Platform",
    version="0.4.0",
    description="FastAPI Backend Control API & Execution Worker using MVC + Service + Repository.",
    lifespan=lifespan,
)
app.include_router(api_router, prefix=settings.api_prefix)


@app.exception_handler(GovernanceError)
async def governance_error_handler(_request: Request, exc: GovernanceError) -> JSONResponse:
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
        content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
    )
