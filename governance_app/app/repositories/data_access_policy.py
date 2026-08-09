from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.models.data_access_policy import DataAccessPolicyVersion, RangerPolicyProjection


class DataAccessPolicyRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def next_version(self, policy_key: str) -> int:
        current = self.session.scalar(
            select(func.max(DataAccessPolicyVersion.version)).where(
                DataAccessPolicyVersion.policy_key == policy_key
            )
        )
        return int(current or 0) + 1

    def create_version(
        self,
        *,
        policy_key: str,
        logical_policy: dict[str, Any],
        checksum: str,
        created_by: str,
    ) -> DataAccessPolicyVersion:
        version = DataAccessPolicyVersion(
            policy_key=policy_key,
            version=self.next_version(policy_key),
            status="DRAFT",
            logical_policy=logical_policy,
            checksum=checksum,
            created_by=created_by,
        )
        self.session.add(version)
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                f"concurrent version creation conflict for {policy_key!r}"
            ) from exc
        return version

    def get_by_id(self, version_id: uuid.UUID | str) -> DataAccessPolicyVersion:
        identifier = uuid.UUID(str(version_id))
        value = self.session.get(DataAccessPolicyVersion, identifier)
        if value is None:
            raise NotFoundError(f"policy version {identifier} was not found")
        return value

    def get_version(self, policy_key: str, version: int) -> DataAccessPolicyVersion:
        value = self.session.scalar(
            select(DataAccessPolicyVersion).where(
                DataAccessPolicyVersion.policy_key == policy_key,
                DataAccessPolicyVersion.version == version,
            )
        )
        if value is None:
            raise NotFoundError(
                f"policy {policy_key!r} version {version} was not found"
            )
        return value

    def list_versions(self, policy_key: str) -> list[DataAccessPolicyVersion]:
        return list(
            self.session.scalars(
                select(DataAccessPolicyVersion)
                .where(DataAccessPolicyVersion.policy_key == policy_key)
                .order_by(DataAccessPolicyVersion.version.asc())
            )
        )

    def get_active(self, policy_key: str) -> DataAccessPolicyVersion | None:
        return self.session.scalar(
            select(DataAccessPolicyVersion).where(
                DataAccessPolicyVersion.policy_key == policy_key,
                DataAccessPolicyVersion.status == "ACTIVE",
            )
        )

    def previous_version(
        self,
        *,
        policy_key: str,
        before_version: int,
    ) -> DataAccessPolicyVersion:
        value = self.session.scalar(
            select(DataAccessPolicyVersion)
            .where(
                DataAccessPolicyVersion.policy_key == policy_key,
                DataAccessPolicyVersion.version < before_version,
            )
            .order_by(DataAccessPolicyVersion.version.desc())
            .limit(1)
        )
        if value is None:
            raise NotFoundError(
                f"policy {policy_key!r} has no immutable version before {before_version}"
            )
        return value

    def activate(self, version: DataAccessPolicyVersion, *, activated_at) -> bool:
        current = self.get_active(version.policy_key)
        if current is not None and current.id == version.id:
            return False

        if current is not None:
            current.status = "INACTIVE"
            self.session.flush()

        version.status = "ACTIVE"
        version.activated_at = activated_at
        self.session.flush()
        return True

    def list_projections(
        self,
        policy_version_id: uuid.UUID | str,
    ) -> list[RangerPolicyProjection]:
        identifier = uuid.UUID(str(policy_version_id))
        return list(
            self.session.scalars(
                select(RangerPolicyProjection)
                .where(RangerPolicyProjection.policy_version_id == identifier)
                .order_by(
                    RangerPolicyProjection.projection_type.asc(),
                    RangerPolicyProjection.ranger_policy_name.asc(),
                )
            )
        )

    def upsert_desired_projections(
        self,
        *,
        policy_version_id: uuid.UUID | str,
        projections: Iterable[Any],
        reset_status: bool,
    ) -> list[RangerPolicyProjection]:
        identifier = uuid.UUID(str(policy_version_id))
        existing = {
            row.ranger_policy_name: row
            for row in self.list_projections(identifier)
        }
        desired_names: set[str] = set()
        rows: list[RangerPolicyProjection] = []

        for projection in projections:
            name = str(projection.ranger_policy_name)
            desired_names.add(name)
            row = existing.get(name)
            if row is None:
                row = RangerPolicyProjection(
                    policy_version_id=identifier,
                    projection_type=str(projection.projection_type),
                    projection_key=str(projection.projection_key),
                    ranger_service=str(projection.ranger_service),
                    ranger_policy_name=name,
                    desired_checksum=str(projection.desired_checksum),
                    sync_status="PENDING",
                    reconciliation_details={},
                )
                self.session.add(row)
            else:
                row.projection_type = str(projection.projection_type)
                row.projection_key = str(projection.projection_key)
                row.ranger_service = str(projection.ranger_service)
                row.desired_checksum = str(projection.desired_checksum)
                if reset_status:
                    row.sync_status = "PENDING"
                    row.last_error = None
            rows.append(row)

        # A policy version is immutable. If stored projection rows exist for a
        # projection no longer produced by that same version, that is an internal
        # contract mismatch rather than something to silently delete.
        unexpected = sorted(set(existing) - desired_names)
        if unexpected:
            raise ConflictError(
                "immutable policy version compiled to a different projection set: "
                + ", ".join(unexpected)
            )

        self.session.flush()
        return rows

    def projection_names_for_policy_key(self, policy_key: str) -> set[str]:
        return set(
            self.session.scalars(
                select(RangerPolicyProjection.ranger_policy_name)
                .join(
                    DataAccessPolicyVersion,
                    DataAccessPolicyVersion.id == RangerPolicyProjection.policy_version_id,
                )
                .where(DataAccessPolicyVersion.policy_key == policy_key)
            )
        )

    def projection_rows_by_name(
        self,
        *,
        policy_key: str,
        ranger_policy_name: str,
    ) -> list[RangerPolicyProjection]:
        return list(
            self.session.scalars(
                select(RangerPolicyProjection)
                .join(
                    DataAccessPolicyVersion,
                    DataAccessPolicyVersion.id == RangerPolicyProjection.policy_version_id,
                )
                .where(
                    DataAccessPolicyVersion.policy_key == policy_key,
                    RangerPolicyProjection.ranger_policy_name == ranger_policy_name,
                )
                .order_by(DataAccessPolicyVersion.version.desc())
            )
        )

    def mark_version_projections(
        self,
        *,
        policy_version_id: uuid.UUID | str,
        sync_status: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        for row in self.list_projections(policy_version_id):
            row.sync_status = sync_status
            if details is not None:
                row.reconciliation_details = details
        self.session.flush()
