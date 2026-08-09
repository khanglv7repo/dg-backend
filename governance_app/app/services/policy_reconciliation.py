from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy.orm import Session

from app.clients.ranger import RangerClient, canonical_hash, normalize_policy
from app.core.config import Settings
from app.core.errors import ExternalSystemError
from app.models.job import utcnow
from app.repositories.data_access_policy import DataAccessPolicyRepository
from app.schemas.data_access_policy import LogicalDataAccessPolicy
from app.services.policy_compiler import CompiledProjection, PolicyCompiler


class PolicyReconciliationService:
    """Controller that converges the current ACTIVE logical policy to Ranger."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        ranger_client: RangerClient,
    ) -> None:
        self.session = session
        self.settings = settings
        self.ranger = ranger_client
        self.repository = DataAccessPolicyRepository(session)
        self.compiler = PolicyCompiler(ranger_service_name=settings.ranger_service_name)

    def reconcile(
        self,
        *,
        policy_version_id: str,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        target = self.repository.get_by_id(policy_version_id)
        active = self.repository.get_active(target.policy_key)
        if active is None or active.id != target.id:
            self.repository.mark_version_projections(
                policy_version_id=target.id,
                sync_status="SUPERSEDED",
                details={
                    "reason": "target policy version is not current ACTIVE",
                    "correlation_id": correlation_id,
                },
            )
            return {
                "status": "SUPERSEDED",
                "policy_key": target.policy_key,
                "policy_version_id": str(target.id),
                "ranger_mutations": 0,
            }

        logical = LogicalDataAccessPolicy.model_validate(active.logical_policy)
        compiled = self.compiler.compile(
            policy_key=active.policy_key,
            version=active.version,
            logical_policy=logical,
        )
        projection_rows = self.repository.upsert_desired_projections(
            policy_version_id=active.id,
            projections=compiled,
            reset_status=False,
        )
        rows_by_name = {row.ranger_policy_name: row for row in projection_rows}
        desired_names = {projection.ranger_policy_name for projection in compiled}
        historical_names = self.repository.projection_names_for_policy_key(active.policy_key)

        results: list[dict[str, Any]] = []
        mutations = 0
        for projection in compiled:
            row = rows_by_name[projection.ranger_policy_name]
            result = self._reconcile_projection(
                active=active,
                projection=projection,
                row=row,
                correlation_id=correlation_id,
            )
            if result["status"] == "SUPERSEDED":
                return {
                    "status": "SUPERSEDED",
                    "policy_key": active.policy_key,
                    "policy_version_id": str(active.id),
                    "ranger_mutations": mutations,
                    "projections": results,
                }
            results.append(result)
            if result.get("mutated"):
                mutations += 1

        # Stable Ranger names are reused across immutable versions. A projection
        # removed by the new ACTIVE version (for example a removed mask column)
        # is retired only when the existing Ranger policy proves Backend ownership.
        for stale_name in sorted(historical_names - desired_names):
            retired = self._retire_stale_projection(
                active=active,
                ranger_policy_name=stale_name,
                correlation_id=correlation_id,
            )
            results.append(retired)
            if retired.get("mutated"):
                mutations += 1
            if retired["status"] == "SUPERSEDED":
                return {
                    "status": "SUPERSEDED",
                    "policy_key": active.policy_key,
                    "policy_version_id": str(active.id),
                    "ranger_mutations": mutations,
                    "projections": results,
                }

        overall = (
            "DRY_RUN"
            if any(item["status"] == "DRY_RUN" for item in results)
            else "SYNCHRONIZED"
        )
        return {
            "status": overall,
            "policy_key": active.policy_key,
            "policy_version_id": str(active.id),
            "version": active.version,
            "ranger_mutations": mutations,
            "projections": results,
        }

    def _reconcile_projection(
        self,
        *,
        active,
        projection: CompiledProjection,
        row,
        correlation_id: str | None,
    ) -> dict[str, Any]:
        pre = self.ranger.find_by_name(projection.ranger_policy_name)
        pre_checksum = self._checksum(pre)

        if pre is not None and not self.ranger.owns_policy(
            pre,
            policy_key=active.policy_key,
        ):
            row.sync_status = "UNMANAGED_CONFLICT"
            row.observed_checksum = pre_checksum
            row.last_error = "same Ranger policy name exists without Backend ownership proof"
            row.reconciliation_details = {
                "correlation_id": correlation_id,
                "pre_read": True,
                "unmanaged": True,
            }
            row.last_reconciled_at = utcnow()
            self.session.flush()
            raise ExternalSystemError(
                f"Ranger policy {projection.ranger_policy_name!r} is unmanaged; refusing mutation",
                system="ranger",
                retryable=False,
            )

        # Fence immediately before the helper that may perform an external write.
        if not self._still_active(active.id, active.policy_key):
            self.repository.mark_version_projections(
                policy_version_id=active.id,
                sync_status="SUPERSEDED",
                details={"reason": "ACTIVE version changed before Ranger apply"},
            )
            return {
                "projection": projection.ranger_policy_name,
                "status": "SUPERSEDED",
                "mutated": False,
            }

        apply_result = self.ranger.reconcile_document(
            policy_key=active.policy_key,
            document=projection.document,
            ownership={
                "policy-version": str(active.version),
                "projection-type": projection.projection_type,
                "projection-key": projection.projection_key,
            },
        )
        action = str(apply_result["action"])
        mutated = action in {"CREATE", "UPDATE", "DISABLE"}

        if action == "DRY_RUN":
            row.sync_status = "DRY_RUN"
            row.observed_checksum = pre_checksum
            row.reconciliation_details = {
                "action": action,
                "correlation_id": correlation_id,
                "pre_checksum": pre_checksum,
            }
            row.last_reconciled_at = utcnow()
            self.session.flush()
            return {
                "projection": projection.ranger_policy_name,
                "status": "DRY_RUN",
                "action": action,
                "mutated": False,
            }

        post = self.ranger.find_by_name(projection.ranger_policy_name)
        post_checksum = self._checksum(post)
        converged = (
            post is not None
            and self.ranger.owns_policy(post, policy_key=active.policy_key)
            and normalize_policy(post) == normalize_policy(projection.document)
        )
        if not converged:
            row.sync_status = "MISMATCH"
            row.observed_checksum = post_checksum
            row.ranger_policy_id = self._policy_id(post)
            row.ranger_policy_guid = self._policy_guid(post)
            row.last_error = "Ranger post-write semantic read-back did not converge"
            row.reconciliation_details = {
                "action": action,
                "correlation_id": correlation_id,
                "pre_checksum": pre_checksum,
                "post_checksum": post_checksum,
                "semantic_convergence": False,
            }
            row.last_reconciled_at = utcnow()
            self.session.flush()
            raise ExternalSystemError(
                f"Ranger read-back mismatch for {projection.ranger_policy_name!r}",
                system="ranger",
                retryable=True,
            )

        row.ranger_policy_id = self._policy_id(post)
        row.ranger_policy_guid = self._policy_guid(post)
        row.observed_checksum = post_checksum
        row.sync_status = "SYNCHRONIZED"
        row.last_error = None
        row.reconciliation_details = {
            "action": action,
            "correlation_id": correlation_id,
            "pre_checksum": pre_checksum,
            "post_checksum": post_checksum,
            "semantic_convergence": True,
        }
        row.last_reconciled_at = utcnow()
        self.session.flush()
        return {
            "projection": projection.ranger_policy_name,
            "projection_type": projection.projection_type,
            "status": "SYNCHRONIZED",
            "action": action,
            "mutated": mutated,
            "desired_checksum": projection.desired_checksum,
            "observed_checksum": post_checksum,
        }

    def _retire_stale_projection(
        self,
        *,
        active,
        ranger_policy_name: str,
        correlation_id: str | None,
    ) -> dict[str, Any]:
        observed = self.ranger.find_by_name(ranger_policy_name)
        rows = self.repository.projection_rows_by_name(
            policy_key=active.policy_key,
            ranger_policy_name=ranger_policy_name,
        )
        if observed is None:
            self._mark_historical_rows(
                rows,
                status="RETIRED",
                details={"action": "NO_CHANGE", "reason": "Ranger policy absent"},
            )
            return {
                "projection": ranger_policy_name,
                "status": "RETIRED",
                "action": "NO_CHANGE",
                "mutated": False,
            }

        if not self.ranger.owns_policy(observed, policy_key=active.policy_key):
            self._mark_historical_rows(
                rows,
                status="UNMANAGED",
                details={
                    "action": "UNTOUCHED",
                    "reason": "Ranger policy lacks Backend ownership proof",
                },
            )
            return {
                "projection": ranger_policy_name,
                "status": "UNMANAGED",
                "action": "UNTOUCHED",
                "mutated": False,
            }

        if not bool(observed.get("isEnabled", True)):
            self._mark_historical_rows(
                rows,
                status="RETIRED",
                details={"action": "NO_CHANGE", "reason": "already disabled"},
            )
            return {
                "projection": ranger_policy_name,
                "status": "RETIRED",
                "action": "NO_CHANGE",
                "mutated": False,
            }

        if not self._still_active(active.id, active.policy_key):
            self.repository.mark_version_projections(
                policy_version_id=active.id,
                sync_status="SUPERSEDED",
                details={"reason": "ACTIVE version changed before stale projection retirement"},
            )
            return {
                "projection": ranger_policy_name,
                "status": "SUPERSEDED",
                "mutated": False,
            }

        disabled = normalize_policy(observed) or {}
        disabled["isEnabled"] = False
        retired_key = hashlib.sha256(ranger_policy_name.encode()).hexdigest()[:12]
        apply_result = self.ranger.reconcile_document(
            policy_key=active.policy_key,
            document=disabled,
            ownership={
                "policy-version": str(active.version),
                "projection-type": "RETIRED",
                "projection-key": f"retired:{retired_key}",
            },
        )
        action = str(apply_result["action"])
        if action == "DRY_RUN":
            self._mark_historical_rows(
                rows,
                status="DRY_RUN",
                details={"action": action, "correlation_id": correlation_id},
            )
            return {
                "projection": ranger_policy_name,
                "status": "DRY_RUN",
                "action": action,
                "mutated": False,
            }

        post = self.ranger.find_by_name(ranger_policy_name)
        converged = (
            post is not None
            and self.ranger.owns_policy(post, policy_key=active.policy_key)
            and bool(post.get("isEnabled", True)) is False
        )
        if not converged:
            self._mark_historical_rows(
                rows,
                status="MISMATCH",
                details={
                    "action": action,
                    "correlation_id": correlation_id,
                    "semantic_convergence": False,
                },
            )
            raise ExternalSystemError(
                f"Ranger read-back mismatch while retiring {ranger_policy_name!r}",
                system="ranger",
                retryable=True,
            )

        self._mark_historical_rows(
            rows,
            status="RETIRED",
            details={
                "action": action,
                "correlation_id": correlation_id,
                "semantic_convergence": True,
            },
        )
        return {
            "projection": ranger_policy_name,
            "status": "RETIRED",
            "action": action,
            "mutated": action in {"CREATE", "UPDATE", "DISABLE"},
        }

    def _still_active(self, version_id, policy_key: str) -> bool:
        self.session.expire_all()
        active = self.repository.get_active(policy_key)
        return active is not None and active.id == version_id

    @staticmethod
    def _checksum(document: dict | None) -> str | None:
        normalized = normalize_policy(document)
        return canonical_hash(normalized) if normalized is not None else None

    @staticmethod
    def _policy_id(document: dict | None) -> str | None:
        if not document or document.get("id") is None:
            return None
        return str(document["id"])

    @staticmethod
    def _policy_guid(document: dict | None) -> str | None:
        if not document or document.get("guid") is None:
            return None
        return str(document["guid"])

    def _mark_historical_rows(
        self,
        rows,
        *,
        status: str,
        details: dict[str, Any],
    ) -> None:
        now = utcnow()
        for row in rows:
            row.sync_status = status
            row.reconciliation_details = details
            row.last_reconciled_at = now
        self.session.flush()
