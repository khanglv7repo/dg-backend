from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.clients.ranger import RangerClient
from app.clients.ranger_tags import RangerTagStoreClient
from app.core.config import Settings
from app.repositories.audit import AuditRepository
from app.rules.policy_mapping import PolicyMappingResolver


class RangerTagPolicyCatalogService:
    """Flow A: reconcile static config/policies.yaml into Ranger dev_tag.

    This service never reads OpenMetadata and never needs an asset FQN.
    """

    def __init__(
        self,
        settings: Settings,
        policy_client: RangerClient,
        tag_store: RangerTagStoreClient,
    ) -> None:
        self.settings = settings
        self.policy_client = policy_client
        self.tag_store = tag_store

    def reconcile(self) -> dict[str, Any]:
        resolver = PolicyMappingResolver.from_path(
            self.settings.resolve_path(self.settings.policy_mappings_path)
        )
        desired = resolver.resolve_all(
            service=self.settings.ranger_tag_service_name,
        )
        desired_names = {policy.name for policy in desired}

        tag_definitions = []
        reconciliations = []
        for policy in desired:
            tag_type = policy.resources["tag"][0]
            tag_definitions.append(
                self.tag_store.ensure_tag_definition(tag_type)
            )
            reconciliations.append(self.policy_client.reconcile(policy))

        stale = []
        for live in self.policy_client.list_policies():
            name = str(live.get("name") or "")
            description = str(live.get("description") or "")
            if not name or name in desired_names:
                continue
            if "managed-by=dg-backend" not in description:
                continue
            if "policy-key=tag-policy:" not in description:
                continue

            result = self.policy_client.reconcile_removal(
                name,
                allow_delete=self.settings.ranger_allow_policy_delete,
            )
            if result is not None:
                stale.append(result)

        return {
            "configuration_version": resolver.configuration_version,
            "tag_service": self.settings.ranger_tag_service_name,
            "resource_service": self.settings.ranger_service_name,
            "tag_definitions": len(tag_definitions),
            "policies": len(desired),
            "reconciliations": reconciliations,
            "stale_reconciliations": stale,
            "dry_run": self.policy_client.dry_run,
        }


class RangerTagAssignmentService:
    """Flow B: sync live Confirmed OM tags to Ranger's tag store.

    There is deliberately no config/policies.yaml lookup here. This is what makes
    classification/tag lifecycle independent from policy lifecycle.
    """

    def __init__(
        self,
        session: Session,
        settings: Settings,
        tag_store: RangerTagStoreClient,
    ) -> None:
        self.session = session
        self.settings = settings
        self.tag_store = tag_store
        self.audit = AuditRepository(session)

    def sync(
        self,
        *,
        entity_type: str,
        entity_fqn: str,
        entity_tags: list[str],
        field_tags: dict[str, list[str]],
        classification_run_id: str | None,
        correlation_id: str | None,
    ) -> dict[str, Any]:
        result = self.tag_store.reconcile_assignments(
            entity_fqn=entity_fqn,
            entity_tags=entity_tags,
            field_tags=field_tags,
        )

        self.audit.record(
            actor_id="system:ranger-tag-sync",
            actor_name="Ranger Tag Assignment Sync",
            action="RANGER_TAG_ASSIGNMENTS_RECONCILED",
            object_type=entity_type,
            object_id=entity_fqn,
            correlation_id=correlation_id,
            details={
                "classification_run_id": classification_run_id,
                "entity_tags": sorted(set(entity_tags)),
                "field_tags": {
                    key: sorted(set(values))
                    for key, values in sorted(field_tags.items())
                },
                "resource_service": self.settings.ranger_service_name,
                "dry_run": self.tag_store.dry_run,
                "result": result,
            },
        )
        return result
