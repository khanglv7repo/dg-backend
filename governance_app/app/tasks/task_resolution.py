"""Celery task handling OM Task resolution follow-up (C1 finding).

OM's `task.entityUpdated` ChangeEvent is trigger-only: it never includes the
actual `status` field transition (A5 PARTIAL finding, Hard Invariant #20),
and even after re-fetching the authoritative status, OM's own "Approve"
action never applies the suggested tag itself (C1 finding) -- Backend must
perform that write explicitly. This task always re-fetches
GET /api/v1/tasks/{id} rather than trusting the event payload.
"""
from __future__ import annotations

import logging

from app.celery_app import app
from app.clients.openmetadata import OpenMetadataClient
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services.openmetadata_governance import ConfirmedTagApplicationService

logger = logging.getLogger(__name__)


@app.task(
    name="app.tasks.task_resolution.resolve_task_followup",
    bind=True,
    max_retries=3,
)
def resolve_task_followup(self, *, task_id: str, correlation_id: str | None = None) -> dict:
    settings = get_settings()
    token = (
        settings.openmetadata_execution_bot_token.get_secret_value()
        if settings.openmetadata_execution_bot_token
        else None
    )
    om_client = OpenMetadataClient(
        base_url=settings.openmetadata_base_url,
        token=token,
        timeout=settings.openmetadata_timeout_seconds,
    )
    session = SessionLocal()
    try:
        task = om_client.get_task(task_id)
        status_value = str(task.get("status") or "")
        task_type = str(task.get("type") or "")

        if status_value != "Approved" or task_type != "TagUpdate":
            return {
                "task_id": task_id,
                "status": status_value,
                "type": task_type,
                "action": "SKIPPED",
            }

        if not settings.auto_apply_tag_enabled:
            logger.info(
                "AUTO_APPLY_TAG kill switch is OFF, skipping tag-apply for task_id=%s",
                task_id,
            )
            return {
                "task_id": task_id,
                "status": status_value,
                "action": "SKIPPED_KILL_SWITCH",
            }

        about = task.get("about") or {}
        entity_type = str(about.get("type") or "table")
        entity_fqn = str(about.get("fullyQualifiedName") or "")
        suggested_tags = list((task.get("payload") or {}).get("suggestedTags") or [])

        if not entity_fqn or not suggested_tags:
            logger.warning(
                "Approved TagUpdate task_id=%s missing entity_fqn or suggestedTags, skipping",
                task_id,
            )
            return {
                "task_id": task_id,
                "status": status_value,
                "action": "SKIPPED_MISSING_DATA",
            }

        result = ConfirmedTagApplicationService(
            session,
            om_client,
            bot_name=settings.openmetadata_execution_bot_name,
        ).apply(
            classification_run_id=None,
            entity_type=entity_type,
            entity_fqn=entity_fqn,
            entity_tags=suggested_tags,
            field_tags={},
            correlation_id=correlation_id,
        )
        return {
            "task_id": task_id,
            "status": status_value,
            "action": "TAGS_APPLIED",
            **result,
        }
    finally:
        session.close()
        om_client.close()
