"""Celery task for watermark-based unclassified-asset discovery.

Replaces the legacy JobType.DISCOVER_UNCLASSIFIED_ASSETS JobRepository dispatch
(docs/13_IMPLEMENTATION_SPEC.md section 9 job engine decision).
Wraps the existing handle_discover_unclassified_assets handler unchanged.
"""
from __future__ import annotations

import logging

from app.celery_app import app
from app.core.config import get_settings
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)


@app.task(
    name="app.tasks.discovery.discover_unclassified_assets",
    bind=True,
    max_retries=3,
)
def discover_unclassified_assets(self, *, payload: dict) -> dict:
    """Celery replacement for the legacy DISCOVER_UNCLASSIFIED_ASSETS job type.

    Drives watermark-based discovery of unclassified assets via
    AssetDiscoveryService.discover(). At-least-once delivery is safe because
    AssetDiscoveryService uses a watermark/offset pattern so re-running the
    discovery is idempotent.
    """
    from app.jobs.handlers import handle_discover_unclassified_assets

    settings = get_settings()
    session = SessionLocal()
    try:
        return handle_discover_unclassified_assets(session, settings, payload)
    except Exception as exc:
        logger.exception(
            "discover_unclassified_assets failed (attempt %d): %s",
            self.request.retries + 1,
            exc,
        )
        raise self.retry(exc=exc)
    finally:
        session.close()
