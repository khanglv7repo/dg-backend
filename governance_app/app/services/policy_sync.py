
from __future__ import annotations

import hashlib
import json

from sqlalchemy.orm import Session

from app.clients.ranger import RangerClient
from app.core.config import Settings
from app.models.enums import JobType, ReconciliationAction
from app.repositories.audit import AuditRepository
from app.repositories.jobs import JobRepository
from app.rules.policy_mapping import PolicyMappingResolver


class PolicySyncService:
    def __init__(self, session: Session, settings: Settings, ranger: RangerClient) -> None:
        self.session = session
        self.settings = settings
        self.ranger = ranger
        self.audit = AuditRepository(session)

    def sync(
        self,
        *,
        entity_fqn: str,
        tags: list[str],
        field_paths: dict[str, list[str]],
        classification_run_id: str | None,
        correlation_id: str | None,
    ) -> dict:
        resolver = PolicyMappingResolver.from_path(
            self.settings.resolve_path(self.settings.policy_mappings_path)
        )
        policies = resolver.resolve_all(
            tags=tags,
            entity_fqn=entity_fqn,
            field_paths=field_paths,
            service=self.settings.ranger_service_name,
        )
        # Reconcile desired active policies for confirmed tags
        active_policy_names = {p.name for p in policies}

        # Check all possible policy mappings to find policies that were removed
        removed_reconciliations: list[dict] = []
        for mapping in resolver.mappings:
            mapped_tag = mapping.get("tag")
            if mapped_tag not in tags:
                # Build target policy name for this entity/field if applicable
                targets = sorted(set(field_paths.get(mapped_tag) or ["*"]))
                for selected_field_path in targets:
                    selected_field_name = (
                        selected_field_path.split(".", 1)[1]
                        if selected_field_path.startswith("columns.")
                        else selected_field_path
                    )
                    replacements = {
                        "${entity_fqn}": entity_fqn,
                        "${field_path}": selected_field_path,
                        "${field_name}": selected_field_name,
                    }
                    policy_key = resolver._render(str(mapping["policy_key"]), replacements)
                    policy_name = resolver._render(str(mapping.get("name", policy_key)), replacements)
                    if policy_name not in active_policy_names:
                        removal_res = self.ranger.reconcile_removal(
                            policy_name, allow_delete=self.settings.ranger_allow_policy_delete
                        )
                        if removal_res:
                            removed_reconciliations.append(removal_res)
                            self.audit.record_reconciliation(
                                policy_key=policy_key,
                                source_version=resolver.configuration_version,
                                desired_hash=removal_res["desired_hash"],
                                observed_hash=removal_res.get("observed_hash"),
                                ranger_policy_id=removal_res.get("policy_id"),
                                action=removal_res["action"],
                                result={
                                    "policy_name": policy_name,
                                    "dry_run": self.ranger.dry_run,
                                    "classification_run_id": classification_run_id,
                                    "tag_removed": True,
                                },
                                correlation_id=correlation_id,
                            )

        if not policies and not removed_reconciliations:
            self.audit.record(
                actor_id="system:executor",
                actor_name="Governance Executor",
                action="NO_RANGER_MAPPING_FOR_CONFIRMED_TAGS",
                object_type="entity",
                object_id=entity_fqn,
                correlation_id=correlation_id,
                details={"tags": sorted(set(tags))},
            )
            return {"reconciliations": [], "verification_jobs": [], "unmapped": True}


        reconciliations: list[dict] = list(removed_reconciliations)
        verification_items: list[tuple[str, object, str]] = []
        for policy in policies:

            result = self.ranger.reconcile(policy)
            reconciliations.append(result)
            self.audit.record_reconciliation(
                policy_key=policy.policy_key,
                source_version=policy.source_version,
                desired_hash=result["desired_hash"],
                observed_hash=result.get("observed_hash"),
                ranger_policy_id=result.get("policy_id"),
                action=result["action"],
                result={
                    "policy_name": policy.name,
                    "dry_run": self.ranger.dry_run,
                    "classification_run_id": classification_run_id,
                },
                correlation_id=correlation_id,
            )
            if result["action"] != ReconciliationAction.DRY_RUN.value:
                for case in policy.verification_cases:
                    verification_items.append((policy.policy_key, case, result["desired_hash"]))

        if self.ranger.dry_run or not verification_items:
            return {"reconciliations": reconciliations, "verification_jobs": []}

        group_material = json.dumps(
            {
                "entity_fqn": entity_fqn,
                "classification_run_id": classification_run_id,
                "items": [
                    (key, desired_hash, case.model_dump())
                    for key, case, desired_hash in verification_items
                ],
            },
            sort_keys=True,
        )
        group_id = hashlib.sha256(group_material.encode()).hexdigest()
        jobs = []
        total = len(verification_items)
        for index, (policy_key, case, desired_hash) in enumerate(verification_items):
            job = JobRepository(self.session).enqueue(
                job_type=JobType.VERIFY_TRINO,
                idempotency_key=f"verify:{group_id}:{index}",
                payload={
                    "verification_group_id": group_id,
                    "verification_total": total,
                    "policy_key": policy_key,
                    "desired_hash": desired_hash,
                    "identity": case.identity,
                    "sql": case.sql,
                    "expected_allowed": case.expected_allowed,
                    "classification_run_id": classification_run_id,
                    "correlation_id": correlation_id,
                },
                correlation_id=correlation_id,
                max_attempts=5,
            )
            jobs.append(str(job.id))
        return {"reconciliations": reconciliations, "verification_jobs": jobs, "group_id": group_id}
