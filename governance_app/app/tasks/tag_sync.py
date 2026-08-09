"""Celery task for OpenMetadata → Ranger tag synchronization."""
from __future__ import annotations

import logging

from app.celery_app import app

logger = logging.getLogger(__name__)


@app.task(name="app.tasks.tag_sync.sync_tags_to_ranger",
          queue="ranger.tag-sync", bind=True, max_retries=3)
def sync_tags_to_ranger(self, *, entity_fqn: str,
                        correlation_id: str | None = None) -> dict:
    """Synchronize tags from OpenMetadata to Ranger ServiceTags.

    This task runs on the dedicated ranger.tag-sync queue with concurrency=1
    to prevent concurrent Ranger state mutations.

    It re-reads the full latest OM tag/resource state (not trusting the
    webhook payload as authoritative), builds the desired Ranger ServiceTags
    snapshot, and reconciles.
    """
    # TODO: Wire to RangerTagSyncService once fully migrated (R3)
    logger.info("sync_tags_to_ranger called for %s", entity_fqn)
    return {"status": "not_implemented", "entity_fqn": entity_fqn}
