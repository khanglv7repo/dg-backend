"""Celery task for OpenMetadata -> Ranger tag synchronization (R3 TAG Vertical Slice).

Runs on dedicated `ranger.tag-sync` queue (concurrency = 1).
Re-reads latest authoritative OpenMetadata Confirmed tag state, reconciles Ranger Tag Store,
and verifies read-back convergence before marking status SYNCHRONIZED.
"""
from __future__ import annotations

import logging
from typing import Any

from app.celery_app import app
from app.clients.openmetadata import OpenMetadataClient
from app.clients.ranger_tags import RangerTagStoreClient
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.repositories.audit import AuditRepository
from app.services.tag_sync_reconciliation import RangerTagSyncReconciliationService

logger = logging.getLogger(__name__)


@app.task(
    name="app.tasks.tag_sync.sync_tags_to_ranger",
    queue="ranger.tag-sync",
    bind=True,
    max_retries=3,
)
def sync_tags_to_ranger(
    self,
    *,
    entity_type: str = "table",
    entity_fqn: str,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Synchronize Confirmed tags from OpenMetadata to Ranger ServiceTags."""
    settings = get_settings()

    om_client = OpenMetadataClient(
        base_url=settings.openmetadata_base_url,
        token=(
            settings.openmetadata_auto_tag_bot_token.get_secret_value()
            if settings.openmetadata_auto_tag_bot_token
            else None
        ),
        timeout=settings.openmetadata_timeout_seconds,
    )

    tag_store = RangerTagStoreClient(
        base_url=settings.ranger_tag_store_base_url,
        username=settings.ranger_service_account,
        password=(
            settings.ranger_service_secret.get_secret_value()
            if settings.ranger_service_secret
            else None
        ),
        resource_service_name=settings.ranger_service_name,
        dry_run=settings.ranger_dry_run,
        timeout=settings.ranger_timeout_seconds,
    )

    with SessionLocal() as session:
        audit_repo = AuditRepository(session)

        try:
            result = RangerTagSyncReconciliationService(
                session,
                om_client,
                tag_store,
            ).synchronize_full_snapshot()

            audit_repo.record(
                actor_id="system:ranger-tag-sync",
                actor_name="Ranger Tag Assignment Sync",
                action="RANGER_TAG_ASSIGNMENTS_RECONCILED",
                object_type=entity_type,
                object_id=entity_fqn,
                correlation_id=correlation_id,
                details={
                    "result": result,
                    "trigger_entity_type": entity_type,
                    "trigger_entity_fqn": entity_fqn,
                },
            )
            session.commit()

            return {
                "status": "SYNCHRONIZED",
                "entity_fqn": entity_fqn,
                "result": result,
            }

        except Exception as exc:
            session.rollback()
            logger.exception("sync_tags_to_ranger failed for %s: %s", entity_fqn, exc)
            raise self.retry(exc=exc, countdown=10)
        finally:
            om_client.close()
            tag_store.close()
