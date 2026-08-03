from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, status

from app.api.dependencies import AppSettings, CurrentActor, DbSession
from app.core.errors import AuthorizationError
from app.schemas.common import AcceptedResponse
from app.schemas.policy_catalog import (
    PolicyImportResponse,
    PolicyRecordResponse,
    PolicySyncRequest,
)
from app.services.policy_catalog import PolicyCatalogService
from app.services.policy_sync import PolicySyncCommandService

router = APIRouter()


@router.post(
    "/import",
    response_model=PolicyImportResponse,
    status_code=status.HTTP_201_CREATED,
)
def import_policy(
    db: DbSession,
    settings: AppSettings,
    actor: CurrentActor,
    document: dict[str, Any] = Body(...),
) -> PolicyImportResponse:
    if not actor.has_any_role("governance-admin"):
        raise AuthorizationError("governance-admin role is required")
    with db.begin():
        policy, created, changed = PolicyCatalogService(db, settings).import_document(
            document,
            actor_id=actor.subject,
            actor_name=actor.display_name,
        )
    return PolicyImportResponse(
        policy=PolicyRecordResponse.model_validate(policy),
        created=created,
        changed=changed,
    )


@router.get("", response_model=list[PolicyRecordResponse])
def list_policies(
    db: DbSession,
    settings: AppSettings,
) -> list[PolicyRecordResponse]:
    values = PolicyCatalogService(db, settings).list_policies()
    return [PolicyRecordResponse.model_validate(item) for item in values]


@router.post(
    "/sync",
    response_model=AcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def sync_policies(
    db: DbSession,
    actor: CurrentActor,
    request: PolicySyncRequest | None = None,
) -> AcceptedResponse:
    if not actor.has_any_role("governance-operator", "governance-admin"):
        raise AuthorizationError("governance-operator or governance-admin role is required")
    body = request or PolicySyncRequest()
    with db.begin():
        job = PolicySyncCommandService(db).enqueue(
            policy_ids=[str(value) for value in body.policy_ids],
            correlation_id=body.correlation_id,
            actor_id=actor.subject,
            actor_name=actor.display_name,
        )
    return AcceptedResponse(job_id=job.id, status=job.status)


@router.get("/{policy_id}", response_model=PolicyRecordResponse)
def get_policy(
    policy_id: UUID,
    db: DbSession,
    settings: AppSettings,
) -> PolicyRecordResponse:
    policy = PolicyCatalogService(db, settings).get_policy(policy_id)
    return PolicyRecordResponse.model_validate(policy)


@router.delete("/{policy_id}", response_model=PolicyRecordResponse)
def disable_policy(
    policy_id: UUID,
    db: DbSession,
    settings: AppSettings,
    actor: CurrentActor,
) -> PolicyRecordResponse:
    if not actor.has_any_role("governance-admin"):
        raise AuthorizationError("governance-admin role is required")
    with db.begin():
        policy = PolicyCatalogService(db, settings).disable(
            policy_id,
            actor_id=actor.subject,
            actor_name=actor.display_name,
        )
    return PolicyRecordResponse.model_validate(policy)
