"""Repository for I1 (race/crash-recovery coordination) around Agent
DIRECT-CREATE DQ TestCase writes. NOT a source of truth for TestCase
existence -- OpenMetadata is (docs/13_IMPLEMENTATION_SPEC.md section 3).
"""
from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.models.job import utcnow
from app.models.testcase_registry import TestCaseRegistry


class TestCaseRegistryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_natural_key_hash(self, natural_key_hash: str) -> TestCaseRegistry | None:
        return (
            self.session.query(TestCaseRegistry)
            .filter(TestCaseRegistry.natural_key_hash == natural_key_hash)
            .first()
        )

    def reserve(
        self,
        *,
        natural_key_hash: str,
        target_entity_fqn: str,
        test_definition_fqn: str,
        stable_test_slot_id: str,
        worker_id: str,
    ) -> tuple[TestCaseRegistry, bool]:
        """Attempt to reserve a natural_key_hash for this worker.

        Returns (registry_row, reserved_by_this_call). If another worker
        already reserved/confirmed this natural_key_hash, returns the
        existing row with reserved_by_this_call=False -- caller decides
        whether that's a 409 (still RESERVED, another worker's in-flight
        attempt) or a safe idempotent no-op (already CONFIRMED, i.e. the
        TestCase genuinely already exists in OM).
        """
        existing = self.get_by_natural_key_hash(natural_key_hash)
        if existing is not None:
            return existing, False

        record = TestCaseRegistry(
            natural_key_hash=natural_key_hash,
            target_entity_fqn=target_entity_fqn,
            test_definition_fqn=test_definition_fqn,
            stable_test_slot_id=stable_test_slot_id,
            reservation_state="RESERVED",
            worker_id=worker_id,
        )
        try:
            self.session.add(record)
            self.session.flush()
            return record, True
        except IntegrityError:
            self.session.rollback()
            existing = self.get_by_natural_key_hash(natural_key_hash)
            if existing is not None:
                return existing, False
            raise

    def confirm(self, record_id, *, om_testcase_id: str) -> None:
        record = self.session.get(TestCaseRegistry, record_id)
        if record:
            record.reservation_state = "CONFIRMED"
            record.om_testcase_id = om_testcase_id
            record.confirmed_at = utcnow()
            self.session.flush()

    def mark_failed(self, record_id) -> None:
        record = self.session.get(TestCaseRegistry, record_id)
        if record:
            record.reservation_state = "FAILED"
            self.session.flush()

    def retry_after_failure(self, record_id, *, worker_id: str) -> TestCaseRegistry | None:
        """Re-arm a FAILED row for a new attempt (I1: a transient OM error --
        e.g. token expiry, network blip -- must not permanently block this
        natural_key_hash forever). Found live during TASK-08's audit: without
        this, a single FAILED write attempt returns 409 CONFLICT for every
        future retry of the exact same logical request, with no way back.
        Re-reserves under the retrying worker_id and clears any stale
        om_testcase_id from the failed attempt."""
        record = self.session.get(TestCaseRegistry, record_id)
        if record is None or record.reservation_state != "FAILED":
            return None
        record.reservation_state = "RESERVED"
        record.worker_id = worker_id
        record.reserved_at = utcnow()
        record.om_testcase_id = None
        record.confirmed_at = None
        self.session.flush()
        return record

    def crash_recovery_candidates(self, *, stale_after_seconds: int) -> list[TestCaseRegistry]:
        """RESERVED rows past their reservation TTL with no om_testcase_id --
        candidates for a crash-recovery pass that re-queries OM by
        deterministic FQN to reconcile (I1's crash-recovery requirement).
        """
        cutoff = utcnow().timestamp() - stale_after_seconds
        return [
            row
            for row in self.session.query(TestCaseRegistry)
            .filter(TestCaseRegistry.reservation_state == "RESERVED")
            .all()
            if row.reserved_at.timestamp() < cutoff
        ]

    @staticmethod
    def raise_conflict_if_reserved_by_other(record: TestCaseRegistry) -> None:
        if record.reservation_state == "RESERVED":
            raise ConflictError(
                f"natural_key_hash {record.natural_key_hash!r} is already reserved "
                f"by worker {record.worker_id!r}"
            )
