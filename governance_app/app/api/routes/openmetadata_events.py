from __future__ import annotations

from typing import Annotated
from uuid import uuid4
from fastapi import APIRouter, Header, Query, status

from app.api.dependencies import AppSettings, DbSession
from app.models.enums import JobType
from app.repositories.jobs import JobRepository
from app.schemas.common import AcceptedResponse
from app.schemas.openmetadata_events import (
    OpenMetadataChangeEventRequest,
    OpenMetadataWebhookResponse,
)
from app.services.openmetadata_event_adapter import OpenMetadataEventAdapterService

router = APIRouter()


@router.post(
    "/events",
    response_model=OpenMetadataWebhookResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def accept_openmetadata_event(
    request: OpenMetadataChangeEventRequest,
    db: DbSession,
    settings: AppSettings,
    x_openmetadata_signature: Annotated[str | None, Header()] = None,
    secret_token: Annotated[str | None, Query(alias="secret")] = None,
) -> OpenMetadataWebhookResponse:
    """Accept a raw OpenMetadata ChangeEvent webhook."""
    adapter = OpenMetadataEventAdapterService(db, settings)
    auth_secret = x_openmetadata_signature or secret_token
    adapter.verify_webhook_token(auth_secret)

    with db.begin():
        job_ids = adapter.process_change_event(request.model_dump(mode="json"))

    return OpenMetadataWebhookResponse(
        status="accepted",
        created_job_ids=job_ids,
    )


@router.post(
    "/discover",
    response_model=AcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def trigger_unclassified_asset_discovery(
    db: DbSession,
    settings: AppSettings,
) -> AcceptedResponse:
    """Manually or scheduled trigger for watermark-based asset discovery."""
    with db.begin():
        job = JobRepository(db).enqueue(
            job_type=JobType.DISCOVER_UNCLASSIFIED_ASSETS,
            idempotency_key=f"discover:manual:{uuid4()}",
            payload={"source": "manual_trigger"},
            max_attempts=3,
        )
    return AcceptedResponse(job_id=job.id, status=job.status)
