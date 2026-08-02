
from __future__ import annotations

from sqlalchemy.orm import Session

from app.clients.trino import TrinoExecutor, query_fingerprint
from app.repositories.audit import AuditRepository


class VerificationService:
    def __init__(self, session: Session, executor: TrinoExecutor) -> None:
        self.session = session
        self.executor = executor
        self.audit = AuditRepository(session)

    def verify(
        self,
        *,
        verification_group_id: str,
        verification_total: int,
        policy_key: str,
        identity: str,
        sql: str,
        expected_allowed: bool,
        classification_run_id: str | None,
        correlation_id: str | None,
    ) -> dict:
        observation = self.executor.execute(identity=identity, sql=sql)
        passed = observation.allowed == expected_allowed
        record = self.audit.record_verification(
            policy_key=policy_key,
            verification_group_id=verification_group_id,
            identity=identity,
            expected_allowed=expected_allowed,
            observed_allowed=observation.allowed,
            passed=passed,
            query_fingerprint=query_fingerprint(sql),
            error_class=observation.error_class,
            error_message=(observation.error_message or "")[:1000] or None,
            duration_ms=observation.duration_ms,
            correlation_id=correlation_id,
        )
        records = self.audit.list_verification_group(verification_group_id)
        complete = len(records) >= verification_total
        if complete:
            all_passed = all(item.passed for item in records)
            self.audit.record(
                actor_id="system:verifier",
                actor_name="Trino Verification",
                action=(
                    "ACCESS_VERIFICATION_GROUP_PASSED"
                    if all_passed
                    else "ACCESS_VERIFICATION_GROUP_FAILED"
                ),
                object_type="verification_group",
                object_id=verification_group_id,
                correlation_id=correlation_id,
                details={
                    "classification_run_id": classification_run_id,
                    "passed": sum(1 for item in records if item.passed),
                    "total": len(records),
                },
            )
        return {
            "verification_id": str(record.id),
            "passed": passed,
            "complete": complete,
            "observed_allowed": observation.allowed,
        }
