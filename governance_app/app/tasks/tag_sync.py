"""Celery task for OpenMetadata -> Ranger tag synchronization (R3 TAG Vertical Slice).

Runs on dedicated `ranger.tag-sync` queue (concurrency = 1).
Re-reads latest authoritative OpenMetadata Confirmed tag state, reconciles Ranger Tag Store,
and verifies read-back convergence before marking status SYNCHRONIZED.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from app.celery_app import app
from app.clients.openmetadata import OpenMetadataClient
from app.clients.ranger_tags import RangerTagStoreClient
from app.core.config import get_settings
from app.core.errors import ExternalSystemError
from app.db.session import SessionLocal
from app.repositories.audit import AuditRepository
from app.repositories.tag_sync_state import TagSyncStateRepository

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
        sync_repo = TagSyncStateRepository(session)
        audit_repo = AuditRepository(session)

        try:
            # 1. Re-read latest authoritative OpenMetadata Confirmed tag snapshot (Latest-state rule)
            snapshot = om_client.get_confirmed_tag_snapshot(
                entity_type=entity_type,
                entity_fqn=entity_fqn,
            )

            entity_tags = snapshot["entity_tags"]
            field_tags = snapshot["field_tags"]

            # Compute checksum of desired state for idempotency check
            canonical = json.dumps(
                {
                    "entity_fqn": entity_fqn,
                    "entity_tags": sorted(entity_tags),
                    "field_tags": {k: sorted(v) for k, v in sorted(field_tags.items())},
                },
                sort_keys=True,
            )
            checksum = hashlib.sha256(canonical.encode()).hexdigest()

            # 2. Reconcile Ranger Tag Store
            result = tag_store.reconcile_assignments(
                entity_fqn=entity_fqn,
                entity_tags=entity_tags,
                field_tags=field_tags,
            )

            # 3. Production read-back verification
            converged = tag_store.verify_convergence(
                entity_fqn=entity_fqn,
                entity_tags=entity_tags,
                field_tags=field_tags,
            )

            if not converged:
                raise ExternalSystemError(
                    f"Ranger tag store failed read-back convergence verification for {entity_fqn}",
                    system="ranger-tag-store",
                    retryable=True,
                )

            # 4. Update TagSyncState in DB ONLY upon verified convergence
            sync_repo.record_sync(
                entity_type=entity_type,
                entity_fqn=entity_fqn,
                status="SYNCHRONIZED",
                checksum=checksum,
                details=result,
            )

            audit_repo.record(
                actor_id="system:ranger-tag-sync",
                actor_name="Ranger Tag Assignment Sync",
                action="RANGER_TAG_ASSIGNMENTS_RECONCILED",
                object_type=entity_type,
                object_id=entity_fqn,
                correlation_id=correlation_id,
                details={
                    "entity_tags": sorted(entity_tags),
                    "field_tags": field_tags,
                    "checksum": checksum,
                    "result": result,
                    "converged": True,
                },
            )
            session.commit()

            return {
                "status": "SYNCHRONIZED",
                "entity_fqn": entity_fqn,
                "checksum": checksum,
                "result": result,
            }

        except Exception as exc:
            session.rollback()
            logger.exception("sync_tags_to_ranger failed for %s: %s", entity_fqn, exc)
            raise self.retry(exc=exc, countdown=10)
        finally:
            om_client.close()
            tag_store.close()
