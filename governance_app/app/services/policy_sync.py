from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.clients.ranger import RangerClient
from app.clients.ranger_tags import RangerTagStoreClient
from app.core.config import Settings
from app.core.errors import ConfigurationError
from app.models.enums import JobType
from app.repositories.audit import AuditRepository
from app.repositories.jobs import JobRepository
from app.repositories.policies import PolicyRepository


class PolicySyncCommandService:
    """Enqueue explicit policy reconciliation without mutating Ranger in HTTP."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.audit = AuditRepository(session)

    def enqueue(
        self,
        *,
        policy_ids: list[str] | None = None,
        correlation_id: str | None = None,
        actor_id: str = "system:policy-sync-command",
        actor_name: str = "Policy Sync Command",
    ):
        normalized_ids = sorted(str(value) for value in (policy_ids or []))
        logical = json.dumps(
            {
                "policy_ids": normalized_ids,
                "nonce": str(uuid.uuid4()),
            },
            sort_keys=True,
        )
        fingerprint = hashlib.sha256(logical.encode()).hexdigest()
        job = JobRepository(self.session).enqueue(
            job_type=JobType.SYNC_RANGER_POLICIES,
            idempotency_key=f"sync-ranger-policies:{fingerprint}",
            payload={
                "policy_ids": normalized_ids,
                "correlation_id": correlation_id,
            },
            correlation_id=correlation_id,
            max_attempts=5,
        )
        self.audit.record(
            actor_id=actor_id,
            actor_name=actor_name,
            action="RANGER_POLICY_SYNC_REQUESTED",
            object_type="policy-catalog",
            object_id="all" if not normalized_ids else ",".join(normalized_ids),
            correlation_id=correlation_id,
            details={"job_id": str(job.id)},
        )
        return job


class RangerPolicyCatalogSyncService:
    """Reconcile PostgreSQL desired-state policies into Ranger."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        policy_clients: dict[str, RangerClient],
        tag_store: RangerTagStoreClient | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.policy_clients = policy_clients
        self.tag_store = tag_store
        self.policies = PolicyRepository(session)
        self.audit = AuditRepository(session)

    def sync(
        self,
        *,
        policy_ids: list[str] | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        if policy_ids:
            desired = [self.policies.get(policy_id) for policy_id in policy_ids]
        else:
            desired = self.policies.list_all()

        reconciliations: list[dict[str, Any]] = []
        for policy in desired:
            client = self.policy_clients.get(policy.service)
            if client is None:
                raise ConfigurationError(
                    f"no Ranger policy client configured for service {policy.service!r}"
                )

            document = dict(policy.document)
            document["isEnabled"] = bool(policy.enabled)

            if policy.policy_kind == "TAG" and self.tag_store is not None:
                tag_resource = (document.get("resources") or {}).get("tag") or {}
                for tag_type in tag_resource.get("values") or []:
                    self.tag_store.ensure_tag_definition(str(tag_type))

            result = client.reconcile_document(
                policy_key=policy.policy_key,
                document=document,
            )
            self.audit.record_reconciliation(
                policy_key=policy.policy_key,
                source_version=f"db-revision:{policy.revision}",
                desired_hash=result["desired_hash"],
                observed_hash=result.get("observed_hash"),
                ranger_policy_id=result.get("policy_id"),
                action=result["action"],
                result=result,
                correlation_id=correlation_id,
            )
            reconciliations.append(
                {
                    "id": str(policy.id),
                    "policy_key": policy.policy_key,
                    "policy_kind": policy.policy_kind,
                    "service": policy.service,
                    "revision": policy.revision,
                    **result,
                }
            )

        self.audit.record(
            actor_id="system:ranger-policy-sync",
            actor_name="Ranger Policy Sync",
            action="RANGER_POLICY_CATALOG_RECONCILED",
            object_type="policy-catalog",
            object_id="all" if not policy_ids else ",".join(policy_ids),
            correlation_id=correlation_id,
            details={
                "policies": len(desired),
                "dry_run": self.settings.ranger_dry_run,
            },
        )
        return {
            "policies": len(desired),
            "reconciliations": reconciliations,
            "dry_run": self.settings.ranger_dry_run,
        }


class RangerTagAssignmentService:
    """Sync live Confirmed OpenMetadata tags to Ranger's tag store."""

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
