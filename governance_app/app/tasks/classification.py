"""Celery tasks for entity classification."""
from __future__ import annotations

import logging

from app.celery_app import app

logger = logging.getLogger(__name__)


@app.task(name="app.tasks.classification.classify_entity", bind=True, max_retries=3)
def classify_entity(self, *, event_id: str, entity_type: str, entity_fqn: str,
                    correlation_id: str | None = None) -> dict:
    """Run deterministic classification on an entity.

    If the deterministic classifier returns MATCH, writes the tag directly
    to OpenMetadata. For NO_MATCH/AMBIGUOUS/CONFLICT, transitions to
    WAITING_AI and enqueues on the ai.classification queue.
    """
    # TODO: Wire to ClassificationService once event_inbox model exists (R3)
    logger.info("classify_entity called for %s (event=%s)", entity_fqn, event_id)
    return {"status": "not_implemented", "event_id": event_id}


@app.task(name="app.tasks.classification.ai_classify_entity",
          queue="ai.classification", bind=True, max_retries=2)
def ai_classify_entity(self, *, execution_id: str, generation: int) -> dict:
    """AI fallback classification for entities that deterministic rules could not classify.

    Receives only execution_id + generation as per the context contract.
    The Agent re-reads full context from OpenMetadata MCP before reasoning.
    """
    # TODO: Wire to Agent runner once Backend MCP exists (R5/R6)
    logger.info("ai_classify_entity called for execution_id=%s generation=%d",
                execution_id, generation)
    return {"status": "not_implemented", "execution_id": execution_id}
