"""Celery task for recovering unfinished workflows."""
from __future__ import annotations

import logging

from app.celery_app import app

logger = logging.getLogger(__name__)


@app.task(name="app.tasks.recovery.retry_unfinished_workflows")
def retry_unfinished_workflows() -> dict:
    """Find and re-dispatch any workflows stuck in intermediate states.

    This task runs periodically via Celery Beat to recover from
    transient failures such as worker crashes or Redis restarts.
    PostgreSQL remains the authoritative workflow state store.
    """
    # TODO: Implement recovery logic once workflow models are migrated (R3)
    logger.debug("Recovery sweep: no-op until workflow models exist")
    return {"recovered": 0}
