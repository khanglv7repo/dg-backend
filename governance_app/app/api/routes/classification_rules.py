from __future__ import annotations

from uuid import UUID

from fastapi import (
    APIRouter,
    File,
    Query,
    UploadFile,
    status,
)

from app.api.dependencies import (
    CurrentActor,
    DbSession,
)
from app.core.errors import AuthorizationError
from app.schemas.classification_rule_sets import (
    ClassificationRuleImportResponse,
    ClassificationRuleSetResponse,
)
from app.services.classification_rule_catalog import (
    ClassificationRuleCatalogService,
)

router = APIRouter()


def _require_reader(actor) -> None:
    if not actor.has_any_role(
        "governance-operator",
        "governance-admin",
    ):
        raise AuthorizationError(
            "governance-operator or governance-admin "
            "role is required"
        )


def _require_admin(actor) -> None:
    if not actor.has_any_role(
        "governance-admin"
    ):
        raise AuthorizationError(
            "governance-admin role is required"
        )


@router.post(
    "/import",
    response_model=ClassificationRuleImportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def import_classification_rules(
    db: DbSession,
    actor: CurrentActor,
    file: UploadFile = File(...),
    activate: bool = Query(default=True),
) -> ClassificationRuleImportResponse:
    _require_admin(actor)

    payload = await file.read(
        2 * 1024 * 1024 + 1
    )

    with db.begin():
        rule_set, created, activated = (
            ClassificationRuleCatalogService(
                db
            ).import_json(
                payload,
                filename=file.filename,
                actor_id=actor.subject,
                actor_name=actor.display_name,
                activate=activate,
            )
        )

    return ClassificationRuleImportResponse(
        rule_set=(
            ClassificationRuleSetResponse.model_validate(
                rule_set
            )
        ),
        created=created,
        activated=activated,
    )


@router.get(
    "",
    response_model=list[
        ClassificationRuleSetResponse
    ],
)
def list_classification_rule_sets(
    db: DbSession,
    actor: CurrentActor,
) -> list[ClassificationRuleSetResponse]:
    _require_reader(actor)
    values = (
        ClassificationRuleCatalogService(
            db
        ).list_rule_sets()
    )
    return [
        ClassificationRuleSetResponse.model_validate(
            value
        )
        for value in values
    ]


@router.get(
    "/active",
    response_model=ClassificationRuleSetResponse,
)
def get_active_classification_rule_set(
    db: DbSession,
    actor: CurrentActor,
) -> ClassificationRuleSetResponse:
    _require_reader(actor)
    value = (
        ClassificationRuleCatalogService(
            db
        ).get_active()
    )
    return (
        ClassificationRuleSetResponse.model_validate(
            value
        )
    )


@router.post(
    "/{rule_set_id}/activate",
    response_model=ClassificationRuleImportResponse,
)
def activate_classification_rule_set(
    rule_set_id: UUID,
    db: DbSession,
    actor: CurrentActor,
) -> ClassificationRuleImportResponse:
    _require_admin(actor)

    with db.begin():
        rule_set, activated = (
            ClassificationRuleCatalogService(
                db
            ).activate(
                rule_set_id,
                actor_id=actor.subject,
                actor_name=actor.display_name,
            )
        )

    return ClassificationRuleImportResponse(
        rule_set=(
            ClassificationRuleSetResponse.model_validate(
                rule_set
            )
        ),
        created=False,
        activated=activated,
    )
