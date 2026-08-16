from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from app.clients.openmetadata import OpenMetadataClient
from app.clients.ranger_tags import RangerTagStoreClient
from app.core.errors import ExternalSystemError
from app.repositories.tag_sync_state import TagSyncStateRepository


class RangerTagSyncReconciliationService:
    """Build and reconcile the full Backend-owned Ranger tag desired state."""

    def __init__(
        self,
        session: Session,
        om_client: OpenMetadataClient,
        tag_store: RangerTagStoreClient,
    ) -> None:
        self.session = session
        self.om_client = om_client
        self.tag_store = tag_store
        self.sync_repo = TagSyncStateRepository(session)

    def synchronize_full_snapshot(self) -> dict[str, Any]:
        snapshots = self.om_client.list_confirmed_table_tag_snapshots()
        desired = self._desired_service_state(snapshots)
        resource_scope = self._desired_resource_scope(desired)
        entity_scope = self._desired_entity_scope(snapshots)
        checksum = self._checksum(desired)

        if getattr(self.tag_store, "dry_run", False):
            per_entity_results = [
                self.tag_store.reconcile_assignments(
                    entity_fqn=snapshot["entity_fqn"],
                    entity_tags=list(snapshot["entity_tags"]),
                    field_tags=dict(snapshot["field_tags"]),
                )
                for snapshot in snapshots
            ]
            actual_before: set[tuple[str, str, str]] = set()
            actual_after: set[tuple[str, str, str]] = desired
            removed: list[dict[str, str]] = []
        else:
            actual_before = self.tag_store.read_actual_service_state(
                resource_scope=resource_scope,
                entity_scope=entity_scope,
            )
            if not self.tag_store.compare_service_state(desired, actual_before):
                per_entity_results = []
                for snapshot in snapshots:
                    per_entity_results.append(
                        self.tag_store.reconcile_assignments(
                            entity_fqn=snapshot["entity_fqn"],
                            entity_tags=list(snapshot["entity_tags"]),
                            field_tags=dict(snapshot["field_tags"]),
                        )
                    )
                removed = self.tag_store.remove_stale_service_assignments(
                    expected=desired,
                    resource_scope=resource_scope,
                    entity_scope=entity_scope,
                )
            else:
                per_entity_results = []
                removed = []

            actual_after = self.tag_store.read_actual_service_state(
                resource_scope=resource_scope,
                entity_scope=entity_scope,
            )
        if not self.tag_store.compare_service_state(desired, actual_after):
            raise ExternalSystemError(
                "Ranger tag store failed full-snapshot read-back convergence verification",
                system="ranger-tag-store",
                retryable=True,
            )
        if not getattr(self.tag_store, "dry_run", False):
            self._verify_observability_equivalent_state(snapshots)

        for snapshot in snapshots:
            self.sync_repo.record_sync(
                entity_type=snapshot["entity_type"],
                entity_fqn=snapshot["entity_fqn"],
                status="SYNCHRONIZED",
                checksum=checksum,
                details={
                    "mode": "FULL_SNAPSHOT",
                    "desired_checksum": checksum,
                    "entity_tags": snapshot["entity_tags"],
                    "field_tags": snapshot["field_tags"],
                },
            )

        return {
            "status": "SYNCHRONIZED",
            "mode": "FULL_SNAPSHOT",
            "desired_count": len(desired),
            "desired_checksum": checksum,
            "changed": actual_before != actual_after or bool(per_entity_results) or bool(removed),
            "reconciled_entities": len(per_entity_results),
            "removed": removed,
        }

    @staticmethod
    def _desired_service_state(
        snapshots: list[dict[str, Any]],
    ) -> set[tuple[str, str, str]]:
        desired: set[tuple[str, str, str]] = set()
        for snapshot in snapshots:
            entity_fqn = str(snapshot["entity_fqn"])
            for tag in snapshot.get("entity_tags", []) or []:
                desired.add((entity_fqn, "$entity", str(tag)))
            for field_path, tags in (snapshot.get("field_tags") or {}).items():
                for tag in tags:
                    desired.add((entity_fqn, str(field_path), str(tag)))
        return desired

    @staticmethod
    def _desired_resource_scope(
        desired: set[tuple[str, str, str]],
    ) -> set[tuple[str, str]]:
        return {(entity_fqn, field_path) for entity_fqn, field_path, _ in desired}

    @staticmethod
    def _desired_entity_scope(snapshots: list[dict[str, Any]]) -> set[str]:
        return {str(snapshot["entity_fqn"]) for snapshot in snapshots}

    def _verify_observability_equivalent_state(
        self,
        snapshots: list[dict[str, Any]],
    ) -> None:
        for snapshot in snapshots:
            desired = self._desired_entity_state(snapshot)
            actual = self.tag_store.read_actual_state(str(snapshot["entity_fqn"]))
            if not self.tag_store.compare_state(desired, actual):
                raise ExternalSystemError(
                    "Ranger tag store failed entity read-back convergence verification",
                    system="ranger-tag-store",
                    retryable=True,
                )

    @staticmethod
    def _desired_entity_state(snapshot: dict[str, Any]) -> set[tuple[str, str]]:
        desired: set[tuple[str, str]] = set()
        for tag in snapshot.get("entity_tags", []) or []:
            desired.add(("$entity", str(tag)))
        for field_path, tags in (snapshot.get("field_tags") or {}).items():
            for tag in tags:
                desired.add((str(field_path), str(tag)))
        return desired

    @staticmethod
    def _checksum(desired: set[tuple[str, str, str]]) -> str:
        canonical = json.dumps(sorted(desired), separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()
