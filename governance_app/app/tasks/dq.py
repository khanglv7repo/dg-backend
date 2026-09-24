"""Celery tasks for DQ materialization and crash recovery."""
from __future__ import annotations

import logging

from app.celery_app import app
from app.clients.openmetadata import OpenMetadataClient
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.repositories.testcase_registry import TestCaseRegistryRepository
from app.services.dq_service import DQService

logger = logging.getLogger(__name__)


def _execution_om_client(settings) -> OpenMetadataClient:
    token = (
        settings.openmetadata_execution_bot_token.get_secret_value()
        if settings.openmetadata_execution_bot_token
        else None
    )
    return OpenMetadataClient(
        base_url=settings.openmetadata_base_url,
        token=token,
        timeout=settings.openmetadata_timeout_seconds,
    )


@app.task(
    name="app.tasks.dq.materialize_approved_test_case",
    bind=True,
    max_retries=5,
)
def materialize_approved_test_case(self, *, registry_id: str) -> dict:
    """Materialize one APPROVED Backend DQ spec into OpenMetadata.

    Safe for at-least-once delivery: DQService first searches OM by deterministic
    TestCase name + entityFQN and only creates when absent.
    """
    settings = get_settings()
    om_client = _execution_om_client(settings)
    with SessionLocal() as session:
        try:
            return DQService(
                session,
                settings,
                om_client=om_client,
            ).materialize_approved_test_case(registry_id=registry_id)
        except Exception as exc:
            session.rollback()
            logger.exception(
                "DQ materialization failed for %s: %s",
                registry_id,
                exc,
            )
            raise self.retry(exc=exc, countdown=min(300, 2 ** (self.request.retries + 1)))
        finally:
            om_client.close()


@app.task(name="app.tasks.dq.recover_testcase_registry")
def recover_testcase_registry() -> dict:
    """Recover both legacy create crashes and approved-but-undispatched specs."""
    settings = get_settings()
    reconciled = 0
    still_missing = 0
    redispatched = 0

    with SessionLocal() as session:
        repository = TestCaseRegistryRepository(session)

        # New architecture: an APPROVED spec may exist without a Celery task if
        # broker dispatch failed after the approval commit. Redispatching is
        # idempotent because materialization itself re-reads OM first.
        approved = repository.approved_materialization_candidates()
        for record in approved:
            materialize_approved_test_case.delay(registry_id=str(record.id))
            redispatched += 1

        # Compatibility recovery for rows created under the old direct-create
        # architecture. These RESERVED rows may represent an OM create that
        # succeeded before the local CONFIRM write landed.
        candidates = repository.crash_recovery_candidates(
            stale_after_seconds=settings.dq_registry_reservation_ttl_seconds
        )
        if candidates:
            om_client = _execution_om_client(settings)
            try:
                for record in candidates:
                    spec = dict(record.spec_payload or {})
                    entity_fqn = str(
                        spec.get("target_asset_fqn") or record.target_entity_fqn
                    )
                    observed = om_client.find_test_case_by_entity_and_name(
                        entity_fqn=entity_fqn,
                        name=record.natural_key_hash,
                    )
                    if observed is not None and observed.get("testSuite"):
                        repository.mark_executable(
                            record.id,
                            om_testcase_id=str(observed.get("id") or ""),
                            om_testcase_fqn=str(
                                observed.get("fullyQualifiedName") or ""
                            ),
                        )
                        session.commit()
                        reconciled += 1
                    elif record.lifecycle_state == "APPROVED":
                        materialize_approved_test_case.delay(
                            registry_id=str(record.id)
                        )
                        redispatched += 1
                    else:
                        repository.mark_failed(record.id)
                        session.commit()
                        still_missing += 1
            finally:
                om_client.close()

    return {
        "reconciled": reconciled,
        "still_missing": still_missing,
        "redispatched": redispatched,
    }
