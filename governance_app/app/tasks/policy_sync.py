"""Celery task for R4 logical data-access policy reconciliation to Ranger."""
from __future__ import annotations

import logging

from app.celery_app import app
from app.core.config import get_settings
from app.core.errors import ExternalSystemError
from app.db.session import SessionLocal
from app.repositories.audit import AuditRepository
from app.services.policy_reconciliation import PolicyReconciliationService
from app.services.policy_verification import PolicyRuntimeVerificationService
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
    """Verify synchronized ACTIVE policy projections against Trino runtime.

    Only deterministic checks are executed. Unsupported projection/persona
    combinations are persisted as VERIFICATION_UNAVAILABLE, infrastructure
    failures as VERIFICATION_ERROR, and only a completed mismatching runtime
    observation beyond the propagation window becomes RUNTIME_DRIFT.
    """
    from collections import Counter

    from sqlalchemy import select

    from app.models.data_access_policy import (
        DataAccessPolicyVersion,
        RangerPolicyProjection,
    )
    from app.models.job import utcnow
    from app.schemas.data_access_policy import LogicalDataAccessPolicy

    settings = get_settings()

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
                .where(RangerPolicyProjection.last_reconciled_at.isnot(None))
            ).all()
        )
        if not rows:
            return {
                "status": "NO_PROJECTIONS",
                "confirmed": 0,
                "pending": 0,
                "drift": 0,
                "unavailable": 0,
                "errors": 0,
            }

        if not settings.trino_readonly_enabled:
            now = utcnow()
            for projection, _version in rows:
                projection.verification_status = "VERIFICATION_UNAVAILABLE"
                projection.verification_details = {
                    "status": "VERIFICATION_UNAVAILABLE",
                    "reason": "read-only Trino verification is disabled",
                    "verified_at": now.isoformat(),
                }
                projection.last_verified_at = now
            db.commit()
            return {
                "status": "VERIFICATION_UNAVAILABLE",
                "confirmed": 0,
                "pending": 0,
                "drift": 0,
                "unavailable": len(rows),
                "errors": 0,
                "reason": "read-only Trino verification is disabled",
            }

        verifier = PolicyRuntimeVerificationService(settings)
        audit = AuditRepository(db)
        counts: Counter[str] = Counter()

        for projection, version in rows:
            previous_status = str(
                projection.verification_status or "UNVERIFIED"
            )
            logical = LogicalDataAccessPolicy.model_validate(version.logical_policy)
            result = verifier.verify(
                logical_policy=logical,
                projection_type=projection.projection_type,
                projection_key=projection.projection_key,
                ranger_apply_timestamp=projection.last_reconciled_at,
            )
            current_status = str(result["status"])
            projection.verification_status = current_status
            projection.verification_details = result
            projection.last_verified_at = utcnow()
            counts[current_status] += 1

            audit_details = {
                "policy_version_id": str(version.id),
                "policy_key": version.policy_key,
                "projection_id": str(projection.id),
                "projection_type": projection.projection_type,
                "ranger_policy_name": projection.ranger_policy_name,
                "previous_status": previous_status,
                "current_status": current_status,
                "verification": result,
            }
            if (
                current_status == "RUNTIME_DRIFT"
                and previous_status != "RUNTIME_DRIFT"
            ):
                audit.record(
                    actor_id="system:trino-policy-verifier",
                    actor_name="Trino Policy Runtime Verifier",
                    action="RUNTIME_DRIFT_DETECTED",
                    object_type="ranger_policy_projection",
                    object_id=str(projection.id),
                    correlation_id=None,
                    details=audit_details,
                )
            elif (
                previous_status == "RUNTIME_DRIFT"
                and current_status == "VERIFICATION_CONFIRMED"
            ):
                audit.record(
                    actor_id="system:trino-policy-verifier",
                    actor_name="Trino Policy Runtime Verifier",
                    action="RUNTIME_DRIFT_RESOLVED",
                    object_type="ranger_policy_projection",
                    object_id=str(projection.id),
                    correlation_id=None,
                    details=audit_details,
                )

        db.commit()

    drift = counts["RUNTIME_DRIFT"]
    errors = counts["VERIFICATION_ERROR"]
    pending = counts["VERIFICATION_PENDING"]
    unavailable = counts["VERIFICATION_UNAVAILABLE"]
    confirmed = counts["VERIFICATION_CONFIRMED"]

    overall = (
        "RUNTIME_DRIFT"
        if drift
        else "VERIFICATION_ERROR"
        if errors
        else "VERIFICATION_PENDING"
        if pending
        else "PARTIALLY_VERIFIED"
        if unavailable
        else "VERIFICATION_CONFIRMED"
    )
    return {
        "status": overall,
        "confirmed": confirmed,
        "pending": pending,
        "drift": drift,
        "unavailable": unavailable,
        "errors": errors,
    }
