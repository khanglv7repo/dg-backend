"""Repository for I1 (race/crash-recovery coordination) around Agent
DIRECT-CREATE DQ TestCase writes. NOT a source of truth for TestCase
existence -- OpenMetadata is (docs/13_IMPLEMENTATION_SPEC.md section 3).
"""
from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.models.job import utcnow
from app.models.testcase_registry import TestCaseRegistry


class TestCaseRegistryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, record_id) -> TestCaseRegistry | None:
        identifier = (
            record_id
            if isinstance(record_id, uuid.UUID)
            else uuid.UUID(str(record_id))
        )
        return self.session.get(TestCaseRegistry, identifier)

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
        spec_payload: dict,
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
            spec_payload=spec_payload,
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

    def approved_materialization_candidates(self) -> list[TestCaseRegistry]:
        return list(
            self.session.query(TestCaseRegistry)
            .filter(TestCaseRegistry.lifecycle_state == "APPROVED")
            .filter(TestCaseRegistry.om_testcase_id.is_(None))
            .all()
        )

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
            .filter(TestCaseRegistry.lifecycle_state != "APPROVED")
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

    def approve(self, record_id, *, actor_id: str) -> TestCaseRegistry:
        """Explicit human/operator STAGED -> APPROVED transition.

        Approval never makes the TestCase executable and never invokes
        OpenMetadata. That separate transition requires a verified executable
        TestSuite API contract.
        """
        record = self.session.get(TestCaseRegistry, record_id)
        if record is None:
            raise ConflictError(f"testcase registry row {record_id!r} was not found")
        if record.reservation_state not in {"RESERVED", "CONFIRMED"}:
            raise ConflictError(
                f"testcase {record_id!r} cannot be approved while reservation_state="
                f"{record.reservation_state!r}"
            )
        if record.lifecycle_state == "APPROVED":
            return record
        if record.lifecycle_state != "STAGED":
            raise ConflictError(
                f"testcase {record_id!r} cannot transition from "
                f"{record.lifecycle_state!r} to APPROVED"
            )
        record.lifecycle_state = "APPROVED"
        record.approved_at = utcnow()
        record.approved_by = actor_id
        self.session.flush()
        return record


    def mark_executable(
        self,
        record_id,
        *,
        om_testcase_id: str,
        om_testcase_fqn: str,
    ) -> TestCaseRegistry:
        record = self.session.get(TestCaseRegistry, record_id)
        if record is None:
            raise ConflictError(f"testcase registry row {record_id!r} was not found")
        if record.lifecycle_state not in {"APPROVED", "EXECUTABLE"}:
            raise ConflictError(
                f"testcase {record_id!r} cannot materialize from "
                f"{record.lifecycle_state!r}"
            )
        record.reservation_state = "CONFIRMED"
        record.om_testcase_id = om_testcase_id
        record.om_testcase_fqn = om_testcase_fqn
        record.confirmed_at = utcnow()
        record.lifecycle_state = "EXECUTABLE"
        self.session.flush()
        return record

    def mark_materialization_failed(self, record_id) -> None:
        record = self.session.get(TestCaseRegistry, record_id)
        if record is not None and record.lifecycle_state == "APPROVED":
            record.reservation_state = "FAILED"
            self.session.flush()
