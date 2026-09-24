"""Celery task implementing the C4 conservative fallback (BLOCKED ->
auto-restore-on-override, docs/13_IMPLEMENTATION_SPEC.md section 9).

C4 could not empirically confirm whether OM's native Auto-Classification
overwrites a manually-set enforcement-critical tag, because this deployment
disables the pipeline orchestrator that would run it
(PIPELINE_SERVICE_CLIENT_ENABLED=false). Per Hard Invariant #19 (a human
override is never silently overwritten by AI) this fallback was applied
proactively rather than waiting for empirical confirmation: if a
ChangeEvent shows a tag REMOVED by an actor identified as OM's native
classifier, Backend restores the tag and escalates.
"""
from __future__ import annotations

import logging
from typing import Any

from app.celery_app import app
from app.clients.openmetadata import OpenMetadataClient
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.repositories.audit import AuditRepository
from app.services.openmetadata_governance import ConfirmedTagApplicationService

logger = logging.getLogger(__name__)

# Actor name(s) OM's native classifier/profiler pipeline is expected to use.
# No live confirmation exists (C4 BLOCKED) -- kept as a named constant so the
# exact value can be corrected once a real Auto-Classification run is
# observed, without hunting through call sites.
NATIVE_CLASSIFIER_ACTORS = frozenset({"om-native-classifier"})


@app.task(
    name="app.tasks.tag_override_guard.restore_overridden_tag",
    bind=True,
    max_retries=3,
)
def restore_overridden_tag(
    self,
    *,
    entity_type: str,
    entity_fqn: str,
    removed_tag_fqns: list[str],
    field_path: str | None,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    session = SessionLocal()
    om_client = OpenMetadataClient(
        base_url=settings.openmetadata_base_url,
        token=(
            settings.openmetadata_execution_bot_token.get_secret_value()
            if settings.openmetadata_execution_bot_token
            else None
        ),
        timeout=settings.openmetadata_timeout_seconds,
    )
    try:
        entity_tags = [] if field_path else list(removed_tag_fqns)
        field_tags = {field_path: list(removed_tag_fqns)} if field_path else {}

        result = ConfirmedTagApplicationService(
            session,
            om_client,
            bot_name=settings.openmetadata_execution_bot_name,
        ).apply(
            classification_run_id=None,
            entity_type=entity_type,
            entity_fqn=entity_fqn,
            entity_tags=entity_tags,
            field_tags=field_tags,
            correlation_id=correlation_id,
        )
        AuditRepository(session).record(
            actor_id="system:tag-override-guard",
            actor_name="Tag Override Guard (C4 conservative fallback)",
            action="ENFORCEMENT_CRITICAL_TAG_AUTO_RESTORED",
            object_type=entity_type,
            object_id=entity_fqn,
            correlation_id=correlation_id,
            details={
                "removed_tag_fqns": removed_tag_fqns,
                "field_path": field_path,
                "escalation": "AUTO_RESTORE_APPLIED",
            },
        )
        session.commit()
        logger.warning(
            "Auto-restored tag(s) %s on %s (field_path=%s) after unauthorized "
            "removal by native classifier actor -- escalate to Platform Team",
            removed_tag_fqns,
            entity_fqn,
            field_path,
        )
        return {"action": "RESTORED", **result}
    finally:
        session.close()
        om_client.close()
