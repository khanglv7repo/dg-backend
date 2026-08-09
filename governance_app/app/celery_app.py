"""Celery application configuration for the governance backend.

This module defines the Celery app, broker/backend settings, task autodiscovery,
and Beat schedule entries. It is the single authoritative Celery configuration.
"""
from __future__ import annotations

import os

from celery import Celery

REDIS_URL = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0")

app = Celery(
    "governance",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_default_queue="default",
    task_queues={
        "default": {},
        "ranger.tag-sync": {},
        "ai.classification": {},
    },
    worker_concurrency=4,
    beat_schedule={
        "trigger-openmetadata-ingestion": {
            "task": "app.tasks.ingestion.trigger_openmetadata_ingestion",
            "schedule": float(os.getenv("INGESTION_INTERVAL_SECONDS", "3600")),
        },
        "retry-unfinished-workflows": {
            "task": "app.tasks.recovery.retry_unfinished_workflows",
            "schedule": 300.0,
        },
    },
)

app.autodiscover_tasks(["app.tasks"])
