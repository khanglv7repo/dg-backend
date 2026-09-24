"""Celery tasks for DQ materialization and crash recovery."""
from __future__ import annotations

import logging

from app.celery_app import app
from app.clients.dq_runner import DQRunnerClient
from app.clients.openmetadata import OpenMetadataClient
from app.core.config import get_settings
from app.core.errors import ConflictError, ExternalSystemError, ValidationError
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
        except ValidationError as exc:
            session.rollback()
            repository = TestCaseRegistryRepository(session)
            repository.mark_materialization_failed(registry_id, permanent=True)
            session.commit()
            logger.error(
                "DQ materialization permanently failed validation for %s: %s",
                registry_id,
                exc,
            )
            return {
                "id": registry_id,
                "status": "FAILED",
                "error": exc.message,
                "retryable": False,
            }
        except ExternalSystemError as exc:
            session.rollback()
            if not exc.retryable:
                repository = TestCaseRegistryRepository(session)
                repository.mark_materialization_failed(registry_id, permanent=True)
                session.commit()
                logger.error(
                    "DQ materialization permanently failed for %s: %s",
                    registry_id,
                    exc,
                )
                return {
                    "id": registry_id,
                    "status": "FAILED",
                    "error": exc.message,
                    "retryable": False,
                }
            logger.exception(
                "DQ materialization transient failure for %s: %s",
                registry_id,
                exc,
            )
            raise self.retry(
                exc=exc,
                countdown=min(300, 2 ** (self.request.retries + 1)),
            )
        except Exception as exc:
            session.rollback()
            logger.exception(
                "DQ materialization unexpected failure for %s: %s",
                registry_id,
                exc,
            )
            raise self.retry(
                exc=exc,
                countdown=min(300, 2 ** (self.request.retries + 1)),
            )
        finally:
            om_client.close()


@app.task(
    name="app.tasks.dq.run_executable_test_case",
    bind=True,
    max_retries=3,
)
def run_executable_test_case(
    self,
    *,
    registry_id: str,
    run_id: str,
) -> dict:
    """Run one generation-fenced EXECUTABLE TestCase through metadata test.

    Execution is intentionally AT-LEAST-ONCE, not exactly-once. A network
    timeout can be ambiguous: the external workflow may have started even
    though Backend did not receive the response. Before every retry we first
    reconcile OpenMetadata result state using the durable run_started_at fence;
    if no qualifying result is visible, re-execution is allowed because the DQ
    workflow is read-only against governed data and only appends DQ result
    metadata.
    """
    settings = get_settings()
    om_client = _execution_om_client(settings)
    runner = DQRunnerClient(
        base_url=settings.dq_runner_url,
        timeout=settings.dq_runner_timeout_seconds,
    )

    with SessionLocal() as session:
        service = DQService(session, settings, om_client=om_client)
        try:
            state = service.mark_run_started(
                registry_id=registry_id,
                run_id=run_id,
            )
            if state.get("run_status") == "COMPLETED":
                return state

            recovered = service.latest_result_for_active_run(
                registry_id=registry_id,
                run_id=run_id,
            )
            if recovered is not None:
                return service.complete_run(
                    registry_id=registry_id,
                    run_id=run_id,
                    result=recovered,
                )

            state = service.get(registry_id=registry_id)
            table_fqn = str(state.get("target_entity_fqn") or "")
            test_suite_fqn = str(state.get("om_test_suite_fqn") or "")
            test_case_name = str(state.get("natural_key_hash") or "")
            if not table_fqn or not test_suite_fqn or not test_case_name:
                raise ValidationError(
                    "EXECUTABLE DQ state is missing table, suite, or TestCase identity"
                )

            runner.run_test_case(
                table_fqn=table_fqn,
                test_suite_fqn=test_suite_fqn,
                test_case_name=test_case_name,
            )

            observed = service.latest_result_for_active_run(
                registry_id=registry_id,
                run_id=run_id,
            )
            if observed is None:
                raise ExternalSystemError(
                    "DQ runner completed but OpenMetadata has no result for this run",
                    system="openmetadata",
                    retryable=True,
                )

            return service.complete_run(
                registry_id=registry_id,
                run_id=run_id,
                result=observed,
            )

        except ConflictError as exc:
            session.rollback()
            return {
                "id": registry_id,
                "run_id": run_id,
                "status": "SUPERSEDED",
                "error": exc.message,
            }
        except ValidationError as exc:
            session.rollback()
            try:
                return service.fail_run(
                    registry_id=registry_id,
                    run_id=run_id,
                    error=exc.message,
                )
            except ConflictError:
                return {
                    "id": registry_id,
                    "run_id": run_id,
                    "status": "SUPERSEDED",
                    "error": exc.message,
                }
        except ExternalSystemError as exc:
            session.rollback()
            if exc.retryable and self.request.retries < 3:
                raise self.retry(
                    exc=exc,
                    countdown=min(300, 2 ** (self.request.retries + 1)),
                )
            try:
                return service.fail_run(
                    registry_id=registry_id,
                    run_id=run_id,
                    error=exc.message,
                )
            except ConflictError:
                return {
                    "id": registry_id,
                    "run_id": run_id,
                    "status": "SUPERSEDED",
                    "error": exc.message,
                }
        finally:
            runner.close()
            om_client.close()


@app.task(name="app.tasks.dq.recover_testcase_registry")
def recover_testcase_registry() -> dict:
    """Recover both legacy create crashes and approved-but-undispatched specs."""
    settings = get_settings()
    reconciled = 0
    still_missing = 0
    redispatched = 0
    run_redispatched = 0

    with SessionLocal() as session:
        repository = TestCaseRegistryRepository(session)

        # New architecture: an APPROVED spec may exist without a Celery task if
        # broker dispatch failed after the approval commit. Redispatching is
        # idempotent because materialization itself re-reads OM first.
        approved = repository.approved_materialization_candidates()
        for record in approved:
            materialize_approved_test_case.delay(registry_id=str(record.id))
            redispatched += 1

        stale_runs = repository.run_recovery_candidates(
            queued_stale_after_seconds=settings.dq_registry_reservation_ttl_seconds,
            running_stale_after_seconds=(
                int(settings.dq_runner_timeout_seconds) + 60
            ),
        )
        for record in stale_runs:
            run_executable_test_case.delay(
                registry_id=str(record.id),
                run_id=str(record.active_run_id),
            )
            run_redispatched += 1

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
                        test_suite = observed.get("testSuite") or {}
                        test_suite_fqn = str(
                            test_suite.get("fullyQualifiedName")
                            or test_suite.get("name")
                            or ""
                        )
                        suite = (
                            om_client.get_test_suite_by_name(test_suite_fqn)
                            if test_suite_fqn
                            else {}
                        )
                        is_executable_suite = (
                            suite.get("basic") is True
                            or suite.get("executable") is True
                        )
                        suite_entity = (
                            suite.get("basicEntityReference")
                            or suite.get("executableEntityReference")
                            or {}
                        )
                        suite_entity_fqn = (
                            str(
                                suite_entity.get("fullyQualifiedName")
                                or suite_entity.get("name")
                                or ""
                            ).strip()
                            if isinstance(suite_entity, dict)
                            else ""
                        )
                        if (
                            is_executable_suite
                            and (
                                not suite_entity_fqn
                                or suite_entity_fqn == entity_fqn
                            )
                        ):
                            repository.mark_executable(
                                record.id,
                                om_testcase_id=str(observed.get("id") or ""),
                                om_testcase_fqn=str(
                                    observed.get("fullyQualifiedName") or ""
                                ),
                                om_test_suite_fqn=test_suite_fqn,
                            )
                            session.commit()
                            reconciled += 1
                        else:
                            repository.mark_failed(record.id)
                            session.commit()
                            still_missing += 1
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
        "run_redispatched": run_redispatched,
    }
