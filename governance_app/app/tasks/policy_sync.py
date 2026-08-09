"""Celery task for R4 logical data-access policy reconciliation to Ranger."""
from __future__ import annotations

import logging

from app.celery_app import app
from app.core.config import get_settings
from app.core.errors import ExternalSystemError
from app.db.session import SessionLocal
from app.services.policy_reconciliation import PolicyReconciliationService
from app.services.ranger_client_factory import build_resource_ranger_client

logger = logging.getLogger(__name__)


@app.task(
    name="app.tasks.policy_sync.sync_policy_to_ranger",
    bind=True,
    max_retries=3,
)
def sync_policy_to_ranger(
    self,
    *,
    policy_version_id: str,
    correlation_id: str | None = None,
) -> dict:
    """Converge the task target only while it remains the current ACTIVE version.

    The task payload is durable identity only. Desired state is reconstructed
    from PostgreSQL on every delivery, so at-least-once delivery is safe and a
    stale task cannot treat its original payload as current authority.
    """

    settings = get_settings()
    with SessionLocal() as db:
        ranger = build_resource_ranger_client(settings)
        try:
            service = PolicyReconciliationService(
                db,
                settings,
                ranger_client=ranger,
            )
            try:
                result = service.reconcile(
                    policy_version_id=policy_version_id,
                    correlation_id=correlation_id,
                )
                db.commit()
                return result
            except ExternalSystemError as exc:
                # Reconciliation status/details are durable even when a retry is
                # warranted. Retrying the same ACTIVE version needs no approval.
                db.commit()
                if exc.retryable:
                    raise self.retry(exc=exc)
                raise
            except Exception:
                db.rollback()
                raise
        finally:
            ranger.close()
