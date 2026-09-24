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


@app.task(
    name="app.tasks.policy_sync.sync_legacy_ranger_policy_catalog",
    bind=True,
    max_retries=5,
)
def sync_legacy_ranger_policy_catalog(self, *, payload: dict) -> dict:
    """Celery replacement for the legacy JobType.SYNC_RANGER_POLICIES
    JobRepository dispatch (docs/13_IMPLEMENTATION_SPEC.md section 9).
    Drives the older GovernancePolicy catalog sync path (deprecated
    /api/v1/policies route), distinct from sync_policy_to_ranger above
    (which is the R4 DataAccessPolicyVersion authoritative path).
    """
    from app.jobs.handlers import handle_sync_ranger_policies

    settings = get_settings()
    session = SessionLocal()
    try:
        return handle_sync_ranger_policies(session, settings, payload)
    finally:
        session.close()


@app.task(name="app.tasks.policy_sync.verify_trino_policy_enforcement")
def verify_trino_policy_enforcement() -> dict:
    """Report Trino verification as unavailable until a real read-back plan exists.

    A placeholder check that always returns False must never be interpreted as
    RUNTIME_DRIFT. Drift is a semantic claim about observed runtime behaviour;
    without a projection-specific verification query there is no observation.

    This hotfix therefore fails closed at the verification capability boundary:
    synchronized Ranger projections remain synchronized, no drift audit event is
    emitted, and callers receive an explicit VERIFICATION_UNAVAILABLE count.
    """
    from sqlalchemy import func, select

    from app.models.data_access_policy import RangerPolicyProjection

    with SessionLocal() as db:
        unavailable = int(
            db.execute(
                select(func.count())
                .select_from(RangerPolicyProjection)
                .where(RangerPolicyProjection.sync_status == "SYNCHRONIZED")
                .where(RangerPolicyProjection.last_reconciled_at.isnot(None))
            ).scalar_one()
        )

    status = "NO_PROJECTIONS" if unavailable == 0 else "VERIFICATION_UNAVAILABLE"
    if unavailable:
        logger.warning(
            "Trino runtime verification unavailable for %s synchronized projection(s): "
            "no projection-specific verification query is implemented",
            unavailable,
        )
    return {
        "status": status,
        "confirmed": 0,
        "pending": 0,
        "drift": 0,
        "unavailable": unavailable,
    }
