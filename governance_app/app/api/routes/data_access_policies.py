from __future__ import annotations

from fastapi import APIRouter, status

from app.api.dependencies import AppSettings, CurrentActor, DbSession
from app.core.errors import AuthorizationError, ExternalSystemError
from app.schemas.data_access_policy import (
    ActivationResponse,
    CreatePolicyVersionRequest,
    PolicyPreviewResponse,
    PolicyStatusResponse,
    PolicyVersionResponse,
    PreviewPolicyRequest,
    ProjectionResponse,
    RollbackPolicyRequest,
)
from app.services.data_access_policy import DataAccessPolicyService
from app.services.ranger_client_factory import build_resource_ranger_client
from app.tasks.policy_sync import sync_policy_to_ranger

router = APIRouter()


def _require_reader(actor) -> None:
    if not actor.has_any_role("governance-operator", "governance-admin"):
        raise AuthorizationError(
            "governance-operator or governance-admin role is required"
        )


def _require_admin(actor) -> None:
    if not actor.has_any_role("governance-admin"):
        raise AuthorizationError("governance-admin role is required")


def _dispatch(version_id: str, correlation_id: str | None = None) -> None:
    try:
        sync_policy_to_ranger.delay(
            policy_version_id=version_id,
            correlation_id=correlation_id,
        )
    except Exception as exc:  # broker failure happens after durable commit by design
        raise ExternalSystemError(
            "policy activation is durable but Celery reconciliation publish failed; "
            "retry the same activation to republish without creating new authority",
            system="celery",
            retryable=True,
            details={"policy_version_id": version_id},
        ) from exc


@router.post(
    "/{policy_key}/versions",
    response_model=PolicyVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_policy_version(
    policy_key: str,
    request: CreatePolicyVersionRequest,
    db: DbSession,
    settings: AppSettings,
    actor: CurrentActor,
) -> PolicyVersionResponse:
    _require_admin(actor)
    with db.begin():
        version = DataAccessPolicyService(db, settings).create_version(
            policy_key=policy_key,
            logical_policy=request.logical_policy,
            actor_id=actor.subject,
            actor_name=actor.display_name,
        )
    return PolicyVersionResponse.model_validate(version)


@router.get(
    "/{policy_key}/versions",
    response_model=list[PolicyVersionResponse],
)
def list_policy_versions(
    policy_key: str,
    db: DbSession,
    settings: AppSettings,
    actor: CurrentActor,
) -> list[PolicyVersionResponse]:
    _require_reader(actor)
    versions = DataAccessPolicyService(db, settings).list_versions(policy_key=policy_key)
    return [PolicyVersionResponse.model_validate(value) for value in versions]


@router.get(
    "/{policy_key}/versions/{version}",
    response_model=PolicyVersionResponse,
)
def get_policy_version(
    policy_key: str,
    version: int,
    db: DbSession,
    settings: AppSettings,
    actor: CurrentActor,
) -> PolicyVersionResponse:
    _require_reader(actor)
    value = DataAccessPolicyService(db, settings).get_version(
        policy_key=policy_key,
        version=version,
    )
    return PolicyVersionResponse.model_validate(value)


@router.post(
    "/{policy_key}/preview",
    response_model=PolicyPreviewResponse,
)
def preview_policy(
    policy_key: str,
    request: PreviewPolicyRequest,
    db: DbSession,
    settings: AppSettings,
    actor: CurrentActor,
) -> PolicyPreviewResponse:
    _require_reader(actor)
    ranger = build_resource_ranger_client(settings)
    try:
        return DataAccessPolicyService(
            db,
            settings,
            ranger_client=ranger,
        ).preview(
            policy_key=policy_key,
            logical_policy=request.logical_policy,
        )
    finally:
        ranger.close()


@router.post(
    "/{policy_key}/versions/{version}/activate",
    response_model=ActivationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def activate_policy_version(
    policy_key: str,
    version: int,
    db: DbSession,
    settings: AppSettings,
    actor: CurrentActor,
) -> ActivationResponse:
    _require_admin(actor)
    ranger = build_resource_ranger_client(settings)
    try:
        # TX1: subject reads + ACTIVE transition + desired projection state.
        with db.begin():
            selected, _changed = DataAccessPolicyService(
                db,
                settings,
                ranger_client=ranger,
            ).activate_version(
                policy_key=policy_key,
                version=version,
                actor_id=actor.subject,
                actor_name=actor.display_name,
            )
        version_id = str(selected.id)
        response_version = PolicyVersionResponse.model_validate(selected)
    finally:
        ranger.close()

    # Publish only after TX1 has committed. The task carries durable identity,
    # never compiled/native Ranger JSON.
    _dispatch(version_id)
    return ActivationResponse(version=response_version, dispatched=True)


@router.post(
    "/{policy_key}/rollback",
    response_model=ActivationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def rollback_policy(
    policy_key: str,
    request: RollbackPolicyRequest,
    db: DbSession,
    settings: AppSettings,
    actor: CurrentActor,
) -> ActivationResponse:
    _require_admin(actor)
    ranger = build_resource_ranger_client(settings)
    try:
        with db.begin():
            selected, _changed = DataAccessPolicyService(
                db,
                settings,
                ranger_client=ranger,
            ).rollback(
                policy_key=policy_key,
                target_version=request.target_version,
                actor_id=actor.subject,
                actor_name=actor.display_name,
            )
        version_id = str(selected.id)
        response_version = PolicyVersionResponse.model_validate(selected)
    finally:
        ranger.close()

    _dispatch(version_id)
    return ActivationResponse(version=response_version, dispatched=True)


@router.get(
    "/{policy_key}/status",
    response_model=PolicyStatusResponse,
)
def get_policy_status(
    policy_key: str,
    db: DbSession,
    settings: AppSettings,
    actor: CurrentActor,
) -> PolicyStatusResponse:
    _require_reader(actor)
    active, projections = DataAccessPolicyService(db, settings).status(
        policy_key=policy_key
    )
    return PolicyStatusResponse(
        policy_key=policy_key,
        active_version=(
            PolicyVersionResponse.model_validate(active) if active is not None else None
        ),
        projections=[ProjectionResponse.model_validate(row) for row in projections],
    )
