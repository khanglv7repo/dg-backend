"""DQ TestCase creation service.

Gated by B1-B5 all PASS (planning/evidence/TASK-04/). Implements Agent
DIRECT-CREATE + STAGED for DQ TestCases:

- Deterministic FQN: dg_<sha256(natural_key)[:32]> (B3), used as the
  OM-side idempotency key -- durable identity, NOT optional.
- testcase_registry (I1) is Backend-side coordination/crash-recovery,
  separate from and in addition to the deterministic FQN.
- STAGED = create via REST /api/v1/dataQuality/testCases with the observed
  entityLink shape, left attached only to its auto-generated `basic`
  (logical) TestSuite -- OM's own execution engine never picks it up in
  this deployment (PIPELINE_SERVICE_CLIENT_ENABLED=false), per B2's PASS
  finding.
- EXECUTABLE = a separate, explicit, auditable Backend/Celery-driven
  transition (create/reuse an executable TestSuite + attach), never
  implicit.

natural_key = (target_entity_fqn, test_definition_fqn, stable_test_slot_id)
stable_test_slot_id = rule_id + "::" + test_key if test_key else rule_id
(docs/09_POC_SPECIFICATION.md section 1, B4(a)).
"""
from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy.orm import Session

from app.clients.openmetadata import OpenMetadataClient
from app.core.config import Settings
from app.core.errors import ConflictError, ValidationError
from app.repositories.testcase_registry import TestCaseRegistryRepository


def build_stable_test_slot_id(*, rule_id: str, test_key: str | None) -> str:
    if test_key:
        return f"{rule_id}::{test_key}"
    return rule_id


def build_natural_key_hash(
    *,
    target_entity_fqn: str,
    test_definition_fqn: str,
    stable_test_slot_id: str,
) -> str:
    natural_key = f"{target_entity_fqn}|{test_definition_fqn}|{stable_test_slot_id}"
    digest = hashlib.sha256(natural_key.encode("utf-8")).hexdigest()[:32]
    return f"dg_{digest}"


class DQService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        om_client: OpenMetadataClient,
    ) -> None:
        self.session = session
        self.settings = settings
        self.om_client = om_client
        self.registry = TestCaseRegistryRepository(session)

    def create_staged_test_case(
        self,
        *,
        target_asset_fqn: str,
        test_definition_fqn: str,
        parameter_values: dict[str, Any],
        rule_id: str,
        test_key: str | None,
        column_name: str | None,
        worker_id: str,
    ) -> dict[str, Any]:
        if not target_asset_fqn or not test_definition_fqn or not rule_id:
            raise ValidationError(
                "target_asset_fqn, test_definition_fqn, and rule_id are required"
            )

        stable_test_slot_id = build_stable_test_slot_id(rule_id=rule_id, test_key=test_key)
        natural_key_hash = build_natural_key_hash(
            target_entity_fqn=target_asset_fqn,
            test_definition_fqn=test_definition_fqn,
            stable_test_slot_id=stable_test_slot_id,
        )

        # I1 Defense Layer 1: Backend-side reservation, serializes concurrent
        # workers before any OM call is made.
        record, reserved_now = self.registry.reserve(
            natural_key_hash=natural_key_hash,
            target_entity_fqn=target_asset_fqn,
            test_definition_fqn=test_definition_fqn,
            stable_test_slot_id=stable_test_slot_id,
            worker_id=worker_id,
        )
        self.session.commit()

        if not reserved_now:
            if record.reservation_state == "CONFIRMED":
                # Idempotent: the TestCase already exists in OM under this
                # exact deterministic name. Same logical request -> same
                # result, safe to return as-is (B3's own confirmed
                # duplicate-create-blocked semantics).
                # Found live during TASK-08's audit: `confirmed_at` only
                # means the registry reservation was confirmed (i.e. OM
                # create succeeded) -- it says nothing about OM's own
                # STAGED->EXECUTABLE transition, which is a separate,
                # not-yet-implemented Backend/Celery-driven step (this
                # class's own module docstring). The old code returned
                # "EXECUTABLE" here unconditionally on every idempotent
                # retry, which was simply wrong -- there is currently no
                # registry field that tracks the real OM executable state,
                # so STAGED is always the correct answer until that
                # transition is implemented and its own state is persisted.
                return {
                    "id": str(record.id),
                    "natural_key_hash": natural_key_hash,
                    "om_testcase_id": record.om_testcase_id,
                    "status": "STAGED",
                }
            if record.reservation_state == "FAILED":
                # Found live during TASK-08's audit: a transient failure
                # (OM 401/timeout/etc) must not permanently block this exact
                # natural_key_hash. Re-arm the row under this worker and fall
                # through to the normal create-in-OM path below, instead of
                # returning 409 forever.
                record = self.registry.retry_after_failure(
                    record.id, worker_id=worker_id
                )
                self.session.commit()
                if record is None:
                    # Raced with another worker's retry between our read and
                    # this call -- safe to treat exactly like the ordinary
                    # "reserved by another worker" case below.
                    raise ConflictError(
                        f"natural_key_hash {natural_key_hash!r} is being "
                        "retried by another worker"
                    )
            else:
                # RESERVED by another worker's in-flight attempt -- B3's
                # outer safety net (OM's own 409-on-duplicate) is the last
                # resort; here we fail fast at the coordination layer instead.
                raise ConflictError(
                    f"natural_key_hash {natural_key_hash!r} is already reserved "
                    f"by worker {record.worker_id!r}"
                )

        # I1 Defense Layer 2 (durable OM-side idempotency key, B3): the
        # deterministic name itself prevents duplicate creation even if two
        # Backend processes somehow both reach this point (e.g. after a
        # crash-recovery re-attempt).
        entity_link = self.om_client.build_entity_link(
            entity_type="table",
            entity_fqn=target_asset_fqn,
            field_path=f"columns.{column_name}" if column_name else None,
        )

        try:
            response = self.om_client.create_test_case(
                name=natural_key_hash,
                entity_link=entity_link,
                test_definition_fqn=test_definition_fqn,
                parameter_values=parameter_values,
            )
        except Exception:
            self.registry.mark_failed(record.id)
            self.session.commit()
            raise

        om_testcase_id = str(response.get("id") or "")
        self.registry.confirm(record.id, om_testcase_id=om_testcase_id)
        self.session.commit()

        return {
            "id": str(record.id),
            "natural_key_hash": natural_key_hash,
            "om_testcase_id": om_testcase_id,
            "status": "STAGED",
        }
