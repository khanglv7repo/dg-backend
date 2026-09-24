from __future__ import annotations

from fastapi import APIRouter, status

from app.api.dependencies import AppSettings, CurrentActor, DbSession
from app.core.errors import AuthorizationError
from app.schemas.dq import DQTestCaseCreateRequest, DQTestCaseResponse
from app.services.dq_service import DQService
from app.tasks.dq import materialize_approved_test_case

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
    """Stage a DQ TestCase spec in Backend only.

    Agent identities may create STAGED governance intent but cannot create an
    OpenMetadata TestCase. OM materialization requires explicit operator/admin
    approval through the approval endpoint below.
    """
    _require_agent_or_operator(actor)

    result = DQService(session, settings).create_staged_test_case(
        target_asset_fqn=request.target_asset_fqn,
        test_definition_fqn=request.test_definition_fqn,
        parameter_values=request.parameter_values,
        rule_id=request.rule_id,
        test_key=request.test_key,
        column_name=request.column_name,
        worker_id=request.worker_id,
        rationale=request.rationale,
    )
    return DQTestCaseResponse.model_validate(result)


@router.post(
    "/test-cases/{registry_id}/approve",
    response_model=DQTestCaseResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def approve_test_case(
    registry_id: str,
    session: DbSession,
    settings: AppSettings,
    actor: CurrentActor,
) -> DQTestCaseResponse:
    """Approve a STAGED spec and dispatch Backend-owned OM materialization."""
    if not actor.has_any_role("governance-operator", "governance-admin"):
        raise AuthorizationError(
            "governance-operator or governance-admin role is required for DQ approval"
        )

    result = DQService(session, settings).approve_staged_test_case(
        registry_id=registry_id,
        actor_id=actor.subject,
    )
    task = materialize_approved_test_case.delay(registry_id=registry_id)
    result["materialization_task_id"] = (
        str(task.id) if getattr(task, "id", None) else None
    )
    return DQTestCaseResponse.model_validate(result)
