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


@app.task(name="app.tasks.policy_sync.verify_trino_policy_enforcement")
def verify_trino_policy_enforcement() -> dict:
    """Verify synchronized Ranger projections through real Trino observations.

    Ranger convergence and runtime verification remain separate state machines.
    Only evidence-based contradictions can become RUNTIME_DRIFT.
    """
    from sqlalchemy import select

    from app.models.data_access_policy import (
        DataAccessPolicyVersion,
        RangerPolicyProjection,
    )
    from app.repositories.audit import AuditRepository
    from app.schemas.data_access_policy import LogicalDataAccessPolicy
    from app.services.trino_readonly import TrinoReadonlyService
    from app.services.trino_verification import (
        RUNTIME_DRIFT,
        VERIFICATION_CONFIRMED,
        VERIFICATION_ERROR,
        VERIFICATION_INCONCLUSIVE,
        VERIFICATION_PENDING,
        VERIFICATION_UNAVAILABLE,
        TrinoRuntimeVerificationService,
    )

    settings = get_settings()
    counters = {
        "confirmed": 0,
        "pending": 0,
        "drift": 0,
        "inconclusive": 0,
        "unavailable": 0,
        "error": 0,
    }

    if not settings.trino_readonly_enabled or not settings.trino_readonly_user:
        with SessionLocal() as db:
            rows = list(
                db.scalars(
                    select(RangerPolicyProjection)
                    .join(
                        DataAccessPolicyVersion,
                        DataAccessPolicyVersion.id
                        == RangerPolicyProjection.policy_version_id,
                    )
                    .where(DataAccessPolicyVersion.status == "ACTIVE")
                    .where(RangerPolicyProjection.sync_status == "SYNCHRONIZED")
                )
            )
            for row in rows:
                row.verification_status = VERIFICATION_UNAVAILABLE
                row.verification_details = {
                    "reason": "Trino read-only verification identity is not configured"
                }
                row.last_verified_at = None
            db.commit()
            counters["unavailable"] = len(rows)
        return {
            "status": (
                "NO_PROJECTIONS" if counters["unavailable"] == 0
                else VERIFICATION_UNAVAILABLE
            ),
            **counters,
        }

    trino = TrinoReadonlyService(settings)
    verifier = TrinoRuntimeVerificationService(settings, trino=trino)

    with SessionLocal() as db:
        rows = list(
            db.execute(
                select(RangerPolicyProjection, DataAccessPolicyVersion)
                .join(
                    DataAccessPolicyVersion,
                    DataAccessPolicyVersion.id
                    == RangerPolicyProjection.policy_version_id,
                )
                .where(DataAccessPolicyVersion.status == "ACTIVE")
                .where(RangerPolicyProjection.sync_status == "SYNCHRONIZED")
            ).all()
        )

        audit = AuditRepository(db)
        for projection, version in rows:
            logical = LogicalDataAccessPolicy.model_validate(version.logical_policy)
            observation = verifier.verify(
                projection_type=projection.projection_type,
                projection_key=projection.projection_key,
                logical_policy=logical,
                ranger_apply_timestamp=projection.last_reconciled_at,
            )
            projection.verification_status = observation.status
            projection.verification_details = {
                **observation.details,
                "verification_user": settings.trino_readonly_user,
                "policy_key": version.policy_key,
                "policy_version": version.version,
            }
            from app.models.job import utcnow

            projection.last_verified_at = utcnow()

            if observation.status == VERIFICATION_CONFIRMED:
                counters["confirmed"] += 1
            elif observation.status == VERIFICATION_PENDING:
                counters["pending"] += 1
            elif observation.status == RUNTIME_DRIFT:
                counters["drift"] += 1
                audit.record(
                    actor_id="system:trino-verifier",
                    actor_name="Trino Runtime Verification",
                    action="RUNTIME_DRIFT_DETECTED",
                    object_type="ranger-policy-projection",
                    object_id=str(projection.id),
                    correlation_id=None,
                    details={
                        "policy_key": version.policy_key,
                        "version": version.version,
                        "projection_type": projection.projection_type,
                        "ranger_policy_name": projection.ranger_policy_name,
                        **observation.details,
                    },
                )
            elif observation.status == VERIFICATION_INCONCLUSIVE:
                counters["inconclusive"] += 1
            elif observation.status == VERIFICATION_ERROR:
                counters["error"] += 1
            else:
                counters["unavailable"] += 1

        db.commit()

    if not rows:
        status = "NO_PROJECTIONS"
    elif counters["drift"]:
        status = RUNTIME_DRIFT
    elif counters["error"]:
        status = VERIFICATION_ERROR
    elif counters["pending"]:
        status = VERIFICATION_PENDING
    elif counters["confirmed"] == len(rows):
        status = VERIFICATION_CONFIRMED
    else:
        status = "PARTIAL_VERIFICATION"

    return {"status": status, **counters}
