from __future__ import annotations

from typing import Any

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import NotFoundError, ValidationError
from app.models.data_access_policy import DataAccessPolicyVersion
from app.repositories.data_access_policy import DataAccessPolicyRepository
from app.schemas.data_access_policy import LogicalDataAccessPolicy, normalize_policy_key
from app.services.data_access_policy import DataAccessPolicyService


class PolicyQueryService:
    """Read/query facade over the R4 authoritative policy model."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.repository = DataAccessPolicyRepository(session)
        self.r4 = DataAccessPolicyService(session, settings)

    def get_policy(self, *, policy_key: str, version: int | None = None) -> dict:
        key = self._key(policy_key)
        if version is not None:
            row = self.r4.get_version(policy_key=key, version=version)
        else:
            row, _projections = self.r4.status(policy_key=key)
            if row is None:
                raise NotFoundError(f"policy {key!r} has no ACTIVE version")
        return self._version_document(row)

    def list_policy_versions(self, *, policy_key: str) -> list[dict]:
        key = self._key(policy_key)
        rows = self.r4.list_versions(policy_key=key)
        if not rows:
            raise NotFoundError(f"policy {key!r} has no versions")
        return [self._version_document(row) for row in rows]

    def get_ranger_sync_status(
        self,
        *,
        policy_key: str,
        version: int | None = None,
    ) -> dict:
        key = self._key(policy_key)
        if version is None:
            row = self.repository.get_active(key)
            if row is None:
                raise NotFoundError(f"policy {key!r} has no ACTIVE version")
        else:
            row = self.repository.get_version(key, version)
        projections = self.repository.list_projections(row.id)
        return {
            "policy_key": key,
            "version": row.version,
            "policy_version_id": str(row.id),
            "policy_status": row.status,
            "projections": [
                {
                    "projection_type": item.projection_type,
                    "projection_key": item.projection_key,
                    "ranger_policy_name": item.ranger_policy_name,
                    "ranger_policy_id": item.ranger_policy_id,
                    "ranger_policy_guid": item.ranger_policy_guid,
                    "desired_checksum": item.desired_checksum,
                    "observed_checksum": item.observed_checksum,
                    "sync_status": item.sync_status,
                    "last_error": item.last_error,
                    "reconciliation_details": item.reconciliation_details,
                    "last_reconciled_at": item.last_reconciled_at,
                }
                for item in projections
            ],
        }

    def check_policy_conflict(
        self,
        *,
        policy_key: str,
        logical_policy: LogicalDataAccessPolicy | dict[str, Any],
    ) -> dict:
        """Bounded deterministic overlap checks; no SQL semantic theorem proving."""

        key = self._key(policy_key)
        candidate = self._logical(logical_policy)
        active_rows = list(
            self.session.scalars(
                select(DataAccessPolicyVersion)
                .where(DataAccessPolicyVersion.status == "ACTIVE")
                .order_by(DataAccessPolicyVersion.policy_key.asc())
            )
        )

        conflicts: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        checked: list[str] = []
        candidate_resource = self._resource(candidate)
        candidate_subjects = self._subjects(candidate)

        for row in active_rows:
            if row.policy_key == key:
                # A candidate for the same policy key replaces that key's ACTIVE
                # intent when activated; do not conflict it with itself.
                continue
            checked.append(row.policy_key)
            current = self._logical(row.logical_policy)
            if self._resource(current) != candidate_resource:
                continue
            shared_subjects = sorted(candidate_subjects & self._subjects(current))
            if not shared_subjects:
                continue

            for operation in sorted(set(candidate.access) & set(current.access)):
                left = candidate.access[operation].value
                right = current.access[operation].value
                if left != right:
                    conflicts.append(
                        {
                            "type": "OPPOSITE_ACCESS_DECISION",
                            "policy_key": row.policy_key,
                            "resource": candidate_resource,
                            "subjects": shared_subjects,
                            "operation": operation,
                            "candidate": left,
                            "active": right,
                        }
                    )

            for column in sorted(set(candidate.masks) & set(current.masks)):
                left_mask = candidate.masks[column].value
                right_mask = current.masks[column].value
                if left_mask != right_mask:
                    conflicts.append(
                        {
                            "type": "INCOMPATIBLE_MASK_INTENT",
                            "policy_key": row.policy_key,
                            "resource": candidate_resource,
                            "subjects": shared_subjects,
                            "column": column,
                            "candidate": left_mask,
                            "active": right_mask,
                        }
                    )

            if candidate.row_filter is not None and current.row_filter is not None:
                warnings.append(
                    {
                        "type": "ROW_FILTER_OVERLAP_REQUIRES_REVIEW",
                        "policy_key": row.policy_key,
                        "resource": candidate_resource,
                        "subjects": shared_subjects,
                        "same_expression": candidate.row_filter == current.row_filter,
                        "requires_review": True,
                        "message": (
                            "Multiple row-filter intents overlap on the exact resource/subject; "
                            "Backend does not semantically parse or merge SQL expressions."
                        ),
                    }
                )

        return {
            "policy_key": key,
            "conflict": bool(conflicts),
            "conflicts": conflicts,
            "warnings": warnings,
            "requires_review": bool(warnings),
            "checked_policy_keys": checked,
        }

    @staticmethod
    def _version_document(row) -> dict:
        return {
            "id": str(row.id),
            "policy_key": row.policy_key,
            "version": row.version,
            "status": row.status,
            "logical_policy": row.logical_policy,
            "checksum": row.checksum,
            "created_by": row.created_by,
            "created_at": row.created_at,
            "activated_at": row.activated_at,
        }

    @staticmethod
    def _logical(value: LogicalDataAccessPolicy | dict[str, Any]) -> LogicalDataAccessPolicy:
        if isinstance(value, LogicalDataAccessPolicy):
            return value
        try:
            return LogicalDataAccessPolicy.model_validate(value)
        except PydanticValidationError as exc:
            raise ValidationError(
                "invalid logical data-access policy",
                details={"errors": exc.errors(include_url=False)},
            ) from exc

    @staticmethod
    def _key(value: str) -> str:
        try:
            return normalize_policy_key(value)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    @staticmethod
    def _resource(policy: LogicalDataAccessPolicy) -> dict[str, str]:
        return {
            "catalog": policy.resource.catalog,
            "schema": policy.resource.schema_name,
            "table": policy.resource.table,
        }

    @staticmethod
    def _subjects(policy: LogicalDataAccessPolicy) -> set[str]:
        return {f"{subject.type.value}:{subject.name}" for subject in policy.subjects}
