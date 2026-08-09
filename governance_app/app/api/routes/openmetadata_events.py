from __future__ import annotations

from typing import Annotated, Any
from uuid import uuid4
from fastapi import APIRouter, Header, Query, Request, status

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


def _extract_change_event_payloads(payload: Any) -> list[dict[str, Any]]:
    """Normalize OpenMetadata webhook delivery envelopes to ChangeEvent dicts.

    OpenMetadata alert webhook delivery can send either a bare ChangeEvent or a
    batch/wrapper payload. Keep the public request schema available for contract
    tests, but make the HTTP route tolerant of the real delivery envelope.
    """
    if isinstance(payload, list):
        events: list[dict[str, Any]] = []
        for item in payload:
            events.extend(_extract_change_event_payloads(item))
        return events

    if not isinstance(payload, dict):
        return []

    if payload.get("eventType") and (
        payload.get("entityFullyQualifiedName")
        or payload.get("entityFQN")
        or isinstance(payload.get("entity"), dict)
    ):
        return [payload]

    for key in ("event", "changeEvent", "data", "message", "payload"):
        nested = payload.get(key)
        if nested is not None:
            events = _extract_change_event_payloads(nested)
            if events:
                return events

    for key in ("events", "records"):
        nested = payload.get(key)
        if nested is not None:
            events = _extract_change_event_payloads(nested)
            if events:
                return events

    return [payload]


@router.post(
    "/events",
    response_model=OpenMetadataWebhookResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def accept_openmetadata_event(
    request: Request,
    db: DbSession,
    settings: AppSettings,
    x_openmetadata_signature: Annotated[str | None, Header()] = None,
    secret_token: Annotated[str | None, Query(alias="secret")] = None,
) -> OpenMetadataWebhookResponse:
    """Accept a raw OpenMetadata ChangeEvent webhook."""
    adapter = OpenMetadataEventAdapterService(db, settings)
    auth_secret = x_openmetadata_signature or secret_token
    adapter.verify_webhook_token(auth_secret)

    raw_payload = await request.json()
    results = [
        adapter.process_change_event(event_data)
        for event_data in _extract_change_event_payloads(raw_payload)
    ]
    if not results:
        results = [{"status": "ignored", "purposes": [], "dispatched_tasks": []}]

    res = results[0]
    tasks = [
        task_id
        for result in results
        for task_id in (result.get("dispatched_tasks") or [])
    ]
    purposes = sorted(
        {
            purpose
            for result in results
            for purpose in (result.get("purposes") or [])
        }
    )

    return OpenMetadataWebhookResponse(
        status=res.get("status", "accepted"),
        event_id=res.get("event_id"),
        purposes=purposes,
        dispatched_tasks=tasks,
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
