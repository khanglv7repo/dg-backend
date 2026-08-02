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
        all_field_paths: list[str] | None = None,
        classification_run_id: str | None,
        correlation_id: str | None,
    ) -> dict:
        """Reconcile Ranger from the current confirmed OpenMetadata state.

        ``tags`` and ``field_paths`` represent only Confirmed tags. When
        ``all_field_paths`` is supplied from an OpenMetadata read-back we can
        also safely disable/delete backend-owned per-column policies that are no
        longer part of the desired state.
        """

        resolver = PolicyMappingResolver.from_path(
            self.settings.resolve_path(self.settings.policy_mappings_path)
        )
        policies = resolver.resolve_all(
            tags=tags,
            entity_fqn=entity_fqn,
            field_paths=field_paths,
            service=self.settings.ranger_service_name,
        )
        active_policy_names = {policy.name for policy in policies}

        if all_field_paths is None:
            removed_reconciliations = self._reconcile_removed_policies_legacy(
                resolver=resolver,
                entity_fqn=entity_fqn,
                tags=tags,
                field_paths=field_paths,
                active_policy_names=active_policy_names,
                classification_run_id=classification_run_id,
                correlation_id=correlation_id,
            )
        else:
            removed_reconciliations = self._reconcile_removed_policies(
                resolver=resolver,
                entity_fqn=entity_fqn,
                active_policy_names=active_policy_names,
                all_field_paths=all_field_paths,
                classification_run_id=classification_run_id,
                correlation_id=correlation_id,
            )

        if not policies and not removed_reconciliations:
            action = (
                "NO_CONFIRMED_TAGS_FOR_RANGER"
                if not tags
                else "NO_RANGER_MAPPING_FOR_CONFIRMED_TAGS"
            )
            self.audit.record(
                actor_id="system:executor",
                actor_name="Governance Executor",
                action=action,
                object_type="entity",
                object_id=entity_fqn,
                correlation_id=correlation_id,
                details={"tags": sorted(set(tags))},
            )
            return {
                "reconciliations": [],
                "verification_jobs": [],
                "unmapped": bool(tags),
            }

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
                    verification_items.append(
                        (policy.policy_key, case, result["desired_hash"])
                    )

        if self.ranger.dry_run or not verification_items:
            return {
                "reconciliations": reconciliations,
                "verification_jobs": [],
            }

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

        return {
            "reconciliations": reconciliations,
            "verification_jobs": jobs,
            "group_id": group_id,
        }


    def _reconcile_removed_policies_legacy(
        self,
        *,
        resolver: PolicyMappingResolver,
        entity_fqn: str,
        tags: list[str],
        field_paths: dict[str, list[str]],
        active_policy_names: set[str],
        classification_run_id: str | None,
        correlation_id: str | None,
    ) -> list[dict]:
        """Preserve the v0.4 removal behavior for already-queued legacy jobs."""

        reconciliations: list[dict] = []
        for mapping in resolver.mappings:
            mapped_tag = mapping.get("tag")
            if mapped_tag in tags:
                continue

            targets = sorted(set(field_paths.get(mapped_tag) or ["*"]))
            for selected_field_path in targets:
                policy_key, policy_name = self._render_mapping_identity(
                    resolver=resolver,
                    mapping=mapping,
                    entity_fqn=entity_fqn,
                    selected_field_path=selected_field_path,
                )
                if policy_name in active_policy_names:
                    continue

                result = self.ranger.reconcile_removal(
                    policy_name,
                    allow_delete=self.settings.ranger_allow_policy_delete,
                )
                if result is None:
                    continue

                reconciliations.append(result)
                self.audit.record_reconciliation(
                    policy_key=policy_key,
                    source_version=resolver.configuration_version,
                    desired_hash=result["desired_hash"],
                    observed_hash=result.get("observed_hash"),
                    ranger_policy_id=result.get("policy_id"),
                    action=result["action"],
                    result={
                        "policy_name": policy_name,
                        "dry_run": self.ranger.dry_run,
                        "classification_run_id": classification_run_id,
                        "tag_removed": True,
                        "legacy_payload": True,
                    },
                    correlation_id=correlation_id,
                )
        return reconciliations

    def _reconcile_removed_policies(
        self,
        *,
        resolver: PolicyMappingResolver,
        entity_fqn: str,
        active_policy_names: set[str],
        all_field_paths: list[str],
        classification_run_id: str | None,
        correlation_id: str | None,
    ) -> list[dict]:
        """Disable/delete owned policy instances that are no longer desired.

        The old implementation tried to remove ``...-*`` when a tag disappeared,
        which could never match the concrete policy name that had been created
        for a real column. With the live OpenMetadata snapshot we know every
        current field and can render concrete candidate names deterministically.
        """

        reconciliations: list[dict] = []
        for mapping in resolver.mappings:
            targets = self._mapping_targets_for_removal(mapping, all_field_paths)
            for selected_field_path in targets:
                policy_key, policy_name = self._render_mapping_identity(
                    resolver=resolver,
                    mapping=mapping,
                    entity_fqn=entity_fqn,
                    selected_field_path=selected_field_path,
                )
                if policy_name in active_policy_names:
                    continue

                result = self.ranger.reconcile_removal(
                    policy_name,
                    allow_delete=self.settings.ranger_allow_policy_delete,
                )
                if result is None:
                    continue

                reconciliations.append(result)
                self.audit.record_reconciliation(
                    policy_key=policy_key,
                    source_version=resolver.configuration_version,
                    desired_hash=result["desired_hash"],
                    observed_hash=result.get("observed_hash"),
                    ranger_policy_id=result.get("policy_id"),
                    action=result["action"],
                    result={
                        "policy_name": policy_name,
                        "dry_run": self.ranger.dry_run,
                        "classification_run_id": classification_run_id,
                        "tag_removed": True,
                    },
                    correlation_id=correlation_id,
                )
        return reconciliations

    @staticmethod
    def _mapping_targets_for_removal(
        mapping: dict,
        all_field_paths: list[str],
    ) -> list[str]:
        serialized = json.dumps(mapping, sort_keys=True)
        field_scoped = "${field_name}" in serialized or "${field_path}" in serialized
        if field_scoped:
            return sorted(set(all_field_paths))
        return ["*"]

    @staticmethod
    def _render_mapping_identity(
        *,
        resolver: PolicyMappingResolver,
        mapping: dict,
        entity_fqn: str,
        selected_field_path: str,
    ) -> tuple[str, str]:
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
        policy_name = resolver._render(
            str(mapping.get("name", policy_key)),
            replacements,
        )
        return policy_key, policy_name
