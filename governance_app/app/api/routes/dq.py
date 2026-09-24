from __future__ import annotations

from fastapi import APIRouter, status

from app.api.dependencies import AppSettings, CurrentActor, DbSession
from app.clients.openmetadata import OpenMetadataClient
from app.core.errors import AuthorizationError
from app.schemas.dq import DQTestCaseCreateRequest, DQTestCaseResponse
from app.services.dq_service import DQService

router = APIRouter()


def _require_agent_or_operator(actor) -> None:
    if not actor.has_any_role(
        "governance-agent-bot", "governance-operator", "governance-admin"
    ):
        raise AuthorizationError(
            "governance-agent-bot, governance-operator, or governance-admin role is required"
        )


@router.post(
    "/test-cases",
    response_model=DQTestCaseResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_test_case(
    request: DQTestCaseCreateRequest,
    session: DbSession,
    settings: AppSettings,
    actor: CurrentActor,
) -> DQTestCaseResponse:
    """POST /api/v1/dq/test-cases (docs/13_IMPLEMENTATION_SPEC.md section 4).

    Agent DIRECT-CREATE for DQ TestCase, bounded by the same RBAC shape as
    A2's dg-agent-bot: ViewAll + Create-TestCase-only.
    """
    _require_agent_or_operator(actor)

    token = (
        settings.openmetadata_agent_bot_token.get_secret_value()
        if settings.openmetadata_agent_bot_token
        else None
    )
    om_client = OpenMetadataClient(
        base_url=settings.openmetadata_base_url,
        token=token,
        timeout=settings.openmetadata_timeout_seconds,
    )
    try:
        service = DQService(session, settings, om_client=om_client)
        result = service.create_staged_test_case(
            target_asset_fqn=request.target_asset_fqn,
            test_definition_fqn=request.test_definition_fqn,
            parameter_values=request.parameter_values,
            rule_id=request.rule_id,
            test_key=request.test_key,
            column_name=request.column_name,
            worker_id=request.worker_id,
        )
    finally:
        om_client.close()

    return DQTestCaseResponse.model_validate(result)


@router.post(
    "/test-cases/{registry_id}/approve",
    response_model=DQTestCaseResponse,
    status_code=status.HTTP_200_OK,
)
def approve_test_case(
    registry_id: str,
    session: DbSession,
    settings: AppSettings,
    actor: CurrentActor,
) -> DQTestCaseResponse:
    """Explicit human/operator approval of a STAGED DQ TestCase.

    Agent identities are intentionally excluded. Approval records governance
    intent only; it does not make the TestCase executable or run it.
    """
    if not actor.has_any_role("governance-operator", "governance-admin"):
        raise AuthorizationError(
            "governance-operator or governance-admin role is required for DQ approval"
        )

    token = (
        settings.openmetadata_agent_bot_token.get_secret_value()
        if settings.openmetadata_agent_bot_token
        else None
    )
    om_client = OpenMetadataClient(
        base_url=settings.openmetadata_base_url,
        token=token,
        timeout=settings.openmetadata_timeout_seconds,
    )
    try:
        result = DQService(session, settings, om_client=om_client).approve_staged_test_case(
            registry_id=registry_id,
            actor_id=actor.subject,
        )
    finally:
        om_client.close()

    return DQTestCaseResponse.model_validate(result)
