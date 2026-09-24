"""DQ governance lifecycle for OpenMetadata 2.0.2.

STAGED is Backend-only. A TestCase is not created in OpenMetadata until an
explicit operator/admin approval has occurred. Materialization then creates
(or recovers) the deterministic TestCase; OpenMetadata 2.0.2 automatically
attaches it to the table's Basic TestSuite, which is the executable suite
relationship used by the DQ runtime.

Backend lifecycle:
    STAGED -> APPROVED -> EXECUTABLE

OpenMetadata materialization state:
    RESERVED -> CONFIRMED

These are deliberately separate state machines.
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.clients.openmetadata import OpenMetadataClient
from app.core.config import Settings
from app.core.errors import ConflictError, ExternalSystemError, ValidationError
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
        om_client: OpenMetadataClient | None = None,
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
        rationale: str | None = None,
    ) -> dict[str, Any]:
        """Persist a DQ spec only; never write OpenMetadata on Agent intake."""
        if not target_asset_fqn or not test_definition_fqn or not rule_id:
            raise ValidationError(
                "target_asset_fqn, test_definition_fqn, and rule_id are required"
            )

        stable_test_slot_id = build_stable_test_slot_id(
            rule_id=rule_id,
            test_key=test_key,
        )
        natural_key_hash = build_natural_key_hash(
            target_entity_fqn=target_asset_fqn,
            test_definition_fqn=test_definition_fqn,
            stable_test_slot_id=stable_test_slot_id,
        )
        spec_payload = {
            "target_asset_fqn": target_asset_fqn,
            "test_definition_fqn": test_definition_fqn,
            "parameter_values": parameter_values,
            "rule_id": rule_id,
            "test_key": test_key,
            "column_name": column_name,
            "rationale": rationale,
        }

        record, reserved_now = self.registry.reserve(
            natural_key_hash=natural_key_hash,
            target_entity_fqn=target_asset_fqn,
            test_definition_fqn=test_definition_fqn,
            stable_test_slot_id=stable_test_slot_id,
            worker_id=worker_id,
            spec_payload=spec_payload,
        )
        self.session.commit()

        if not reserved_now and dict(record.spec_payload or {}) != spec_payload:
            raise ConflictError(
                "same DQ natural key already exists with a different staged spec"
            )

        return self._result(record)

    def approve_staged_test_case(
        self,
        *,
        registry_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        """Explicit human/operator STAGED -> APPROVED transition."""
        identifier = self._uuid(registry_id)
        record = self.registry.approve(identifier, actor_id=actor_id)
        self.session.commit()
        return self._result(record)

    def materialize_approved_test_case(
        self,
        *,
        registry_id: str,
    ) -> dict[str, Any]:
        """Create/recover the approved TestCase in OpenMetadata.

        OpenMetadata 2.0.2 creates/resolves the Basic TestSuite during
        TestCaseRepository.prepare(). We require a read-back with non-null
        testSuite before marking the Backend lifecycle EXECUTABLE.
        """
        if self.om_client is None:
            raise ValidationError("OpenMetadata client is required for materialization")

        identifier = self._uuid(registry_id)
        record = self.registry.get(identifier)
        if record is None:
            raise ConflictError(f"testcase registry row {registry_id!r} was not found")
        if record.lifecycle_state == "EXECUTABLE":
            return self._result(record)
        if record.lifecycle_state != "APPROVED":
            raise ConflictError(
                f"testcase {registry_id!r} cannot materialize from "
                f"{record.lifecycle_state!r}"
            )

        spec = dict(record.spec_payload or {})
        target_asset_fqn = str(spec.get("target_asset_fqn") or "")
        if not target_asset_fqn:
            raise ValidationError("staged DQ spec is missing target_asset_fqn")

        observed = self.om_client.find_test_case_by_entity_and_name(
            entity_fqn=target_asset_fqn,
            name=record.natural_key_hash,
        )
        if observed is None:
            entity_link = self.om_client.build_entity_link(
                entity_type="table",
                entity_fqn=target_asset_fqn,
                field_path=(
                    f"columns.{spec['column_name']}"
                    if spec.get("column_name")
                    else None
                ),
            )
            created = self.om_client.create_test_case(
                name=record.natural_key_hash,
                entity_link=entity_link,
                test_definition_fqn=str(spec["test_definition_fqn"]),
                parameter_values=dict(spec.get("parameter_values") or {}),
            )
            created_fqn = str(created.get("fullyQualifiedName") or "")
            observed = (
                self.om_client.get_test_case_by_name(created_fqn)
                if created_fqn
                else self.om_client.find_test_case_by_entity_and_name(
                    entity_fqn=target_asset_fqn,
                    name=record.natural_key_hash,
                )
            )

        if not observed:
            raise ExternalSystemError(
                "OpenMetadata TestCase create/read-back did not return the TestCase",
                system="openmetadata",
                retryable=True,
            )
        if not observed.get("testSuite"):
            raise ExternalSystemError(
                "OpenMetadata TestCase is not linked to a Basic TestSuite",
                system="openmetadata",
                retryable=True,
            )

        om_testcase_id = str(observed.get("id") or "")
        om_testcase_fqn = str(observed.get("fullyQualifiedName") or "")
        if not om_testcase_id or not om_testcase_fqn:
            raise ExternalSystemError(
                "OpenMetadata TestCase read-back is missing id or fullyQualifiedName",
                system="openmetadata",
                retryable=True,
            )

        record = self.registry.mark_executable(
            record.id,
            om_testcase_id=om_testcase_id,
            om_testcase_fqn=om_testcase_fqn,
        )
        self.session.commit()
        return self._result(record)

    @staticmethod
    def _uuid(value: str) -> uuid.UUID:
        try:
            return uuid.UUID(str(value))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValidationError("registry_id must be a UUID") from exc

    @staticmethod
    def _result(record) -> dict[str, Any]:
        return {
            "id": str(record.id),
            "natural_key_hash": record.natural_key_hash,
            "om_testcase_id": record.om_testcase_id,
            "status": record.lifecycle_state,
        }
