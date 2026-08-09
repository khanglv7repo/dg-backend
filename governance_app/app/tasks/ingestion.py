"""Celery task for triggering OpenMetadata ingestion via om-ingest HTTP API."""
from __future__ import annotations

import logging
import os

import httpx

from app.celery_app import app

logger = logging.getLogger(__name__)

OM_INGEST_URL = os.getenv("OM_INGEST_URL", "http://metadata-ingestion:8080")


@app.task(name="app.tasks.ingestion.trigger_openmetadata_ingestion", bind=True, max_retries=2)
def trigger_openmetadata_ingestion(self) -> dict:
    """Trigger an ingestion run via the om-ingest HTTP API.

    This is the Celery Beat replacement for the internal scheduler in the
    metadata-ingestion container. It calls POST /run-now on the om-ingest
    Flask API.
    """
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(f"{OM_INGEST_URL}/run-now")
            response.raise_for_status()
            result = response.json()
            logger.info("Ingestion trigger result: %s", result)
            return result
    except httpx.HTTPError as exc:
        logger.warning("Ingestion trigger failed: %s", exc)
        raise self.retry(exc=exc, countdown=60)
