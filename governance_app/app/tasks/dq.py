"""Celery task for I1 crash-recovery: reconcile RESERVED testcase_registry
rows that are past their reservation TTL against OM by deterministic FQN,
in case a worker crashed after create_test_case succeeded in OM but before
the local CONFIRM write landed (docs/09_POC_SPECIFICATION.md I1).
"""
from __future__ import annotations

import logging

from app.celery_app import app
from app.clients.openmetadata import OpenMetadataClient
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.repositories.testcase_registry import TestCaseRegistryRepository

logger = logging.getLogger(__name__)


@app.task(name="app.tasks.dq.recover_testcase_registry")
def recover_testcase_registry() -> dict:
    settings = get_settings()
    session = SessionLocal()
    reconciled = 0
    still_missing = 0
    try:
        repository = TestCaseRegistryRepository(session)
        candidates = repository.crash_recovery_candidates(
            stale_after_seconds=settings.dq_registry_reservation_ttl_seconds
        )
        if not candidates:
            return {"reconciled": 0, "still_missing": 0}

        token = (
            settings.openmetadata_agent_bot_token.get_secret_value()
            if settings.openmetadata_agent_bot_token
            else None
        )
        om_client = OpenMetadataClient(
            base_url=settings.openmetadata_base_url,
            token=token,
            timeout=settings.openmetadata_timeout_seconds,
        )
        try:
            for record in candidates:
                observed = om_client.get_test_case_by_name(record.natural_key_hash)
                if observed is not None:
                    repository.confirm(
                        record.id,
                        om_testcase_id=str(observed.get("id") or ""),
                    )
                    session.commit()
                    reconciled += 1
                else:
                    # Genuinely never reached OM -- safe to mark FAILED so a
                    # fresh reservation attempt can retry with the same
                    # natural_key_hash later.
                    repository.mark_failed(record.id)
                    session.commit()
                    still_missing += 1
        finally:
            om_client.close()
    finally:
        session.close()
    return {"reconciled": reconciled, "still_missing": still_missing}
