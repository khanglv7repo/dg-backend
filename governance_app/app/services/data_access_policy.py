from __future__ import annotations

from typing import Any

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from app.clients.ranger import RangerClient, normalize_policy
from app.core.config import Settings
from app.core.errors import ConflictError, ValidationError
from app.models.job import utcnow
from app.repositories.audit import AuditRepository
from app.repositories.data_access_policy import DataAccessPolicyRepository
from app.schemas.data_access_policy import (
    LogicalDataAccessPolicy,
    PolicyPreviewResponse,
    PreviewProjection,
    normalize_policy_key,
)
from app.services.policy_compiler import PolicyCompiler


class DataAccessPolicyService:
    """Authoritative Backend lifecycle for logical data-access policy versions."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        ranger_client: RangerClient | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.repository = DataAccessPolicyRepository(session)
        self.audit = AuditRepository(session)
        self.ranger_client = ranger_client
        self.compiler = PolicyCompiler(ranger_service_name=settings.ranger_service_name)

    def create_version(
        self,
        *,
        policy_key: str,
        logical_policy: LogicalDataAccessPolicy | dict[str, Any],
        actor_id: str,
        actor_name: str,
        correlation_id: str | None = None,
    ):
        key = self._policy_key(policy_key)
        parsed = self._logical_policy(logical_policy)
        version = self.repository.create_version(
            policy_key=key,
            logical_policy=parsed.normalized_document(),
            checksum=parsed.checksum(),
            created_by=actor_id,
        )
        self.audit.record(
            actor_id=actor_id,
            actor_name=actor_name,
            action="DATA_ACCESS_POLICY_VERSION_CREATED",
            object_type="data-access-policy-version",
            object_id=str(version.id),
            correlation_id=correlation_id,
            details={
                "policy_key": key,
                "version": version.version,
                "status": version.status,
                "checksum": version.checksum,
            },
        )
        return version

    def get_version(self, *, policy_key: str, version: int):
        return self.repository.get_version(self._policy_key(policy_key), version)

    def list_versions(self, *, policy_key: str):
        return self.repository.list_versions(self._policy_key(policy_key))

    def status(self, *, policy_key: str):
        key = self._policy_key(policy_key)
        active = self.repository.get_active(key)
        projections = self.repository.list_projections(active.id) if active else []
        return active, projections

    def preview(
        self,
        *,
        policy_key: str,
        logical_policy: LogicalDataAccessPolicy | dict[str, Any],
    ) -> PolicyPreviewResponse:
        """Side-effect-free logical validation + compile + Ranger diff preview."""

        key = self._policy_key(policy_key)
        parsed = self._logical_policy(logical_policy)
        self._validate_subjects(parsed)
        candidate_version = self.repository.next_version(key)
        compiled = self.compiler.compile(
            policy_key=key,
            version=candidate_version,
            logical_policy=parsed,
        )

        client = self._ranger()
        previews: list[PreviewProjection] = []
        desired_names = {projection.ranger_policy_name for projection in compiled}
        for projection in compiled:
            current = client.find_by_name(projection.ranger_policy_name)
            if current is None:
                action = "CREATE"
                owned = None
            else:
                owned = client.owns_policy(current, policy_key=key)
                if not owned:
                    action = "UNMANAGED_CONFLICT"
                elif normalize_policy(current) == normalize_policy(projection.document):
                    action = "NO_CHANGE"
                else:
                    action = "UPDATE"
            previews.append(
                PreviewProjection(
                    projection_type=projection.projection_type,
                    projection_key=projection.projection_key,
                    ranger_policy_name=projection.ranger_policy_name,
                    desired_checksum=projection.desired_checksum,
                    action=action,
                    owned_current=owned,
                    current_policy_id=(
                        str(current.get("id"))
                        if isinstance(current, dict) and current.get("id") is not None
                        else None
                    ),
                )
            )

        retire_names: list[str] = []
        for name in sorted(self.repository.projection_names_for_policy_key(key) - desired_names):
            current = client.find_by_name(name)
            if (
                current
                and bool(current.get("isEnabled", True))
                and client.owns_policy(current, policy_key=key)
            ):
                retire_names.append(name)

        return PolicyPreviewResponse(
            policy_key=key,
            candidate_version=candidate_version,
            logical_checksum=parsed.checksum(),
            projections=previews,
            retire_policy_names=retire_names,
        )

    def activate_version(
        self,
        *,
        policy_key: str,
        version: int,
        actor_id: str,
        actor_name: str,
        correlation_id: str | None = None,
        action: str = "DATA_ACCESS_POLICY_VERSION_ACTIVATED",
    ):
        """Persist approved desired authority; never mutates Ranger here."""

        key = self._policy_key(policy_key)
        selected = self.repository.get_version(key, version)
        parsed = self._logical_policy(selected.logical_policy)

        # Ranger owns users/groups. Validation is read-only and occurs before
        # any authoritative version transition or Ranger policy mutation.
        self._validate_subjects(parsed)

        compiled = self.compiler.compile(
            policy_key=key,
            version=selected.version,
            logical_policy=parsed,
        )
        changed = self.repository.activate(selected, activated_at=utcnow())
        self.repository.upsert_desired_projections(
            policy_version_id=selected.id,
            projections=compiled,
            reset_status=True,
        )

        self.audit.record(
            actor_id=actor_id,
            actor_name=actor_name,
            action=action if changed else "DATA_ACCESS_POLICY_ACTIVATION_NO_CHANGE",
            object_type="data-access-policy-version",
            object_id=str(selected.id),
            correlation_id=correlation_id,
            details={
                "policy_key": key,
                "version": selected.version,
                "logical_checksum": selected.checksum,
                "projection_count": len(compiled),
                "authority_changed": changed,
            },
        )
        return selected, changed

    def rollback(
        self,
        *,
        policy_key: str,
        target_version: int | None,
        actor_id: str,
        actor_name: str,
        correlation_id: str | None = None,
    ):
        key = self._policy_key(policy_key)
        active = self.repository.get_active(key)
        if active is None:
            raise ConflictError(f"policy {key!r} has no ACTIVE version to roll back")
        target = (
            self.repository.get_version(key, target_version)
            if target_version is not None
            else self.repository.previous_version(
                policy_key=key,
                before_version=active.version,
            )
        )
        if target.id == active.id:
            raise ConflictError("rollback target is already ACTIVE")
        return self.activate_version(
            policy_key=key,
            version=target.version,
            actor_id=actor_id,
            actor_name=actor_name,
            correlation_id=correlation_id,
            action="DATA_ACCESS_POLICY_ROLLED_BACK",
        )

    def _validate_subjects(self, logical_policy: LogicalDataAccessPolicy) -> None:
        client = self._ranger()
        missing: list[dict[str, str]] = []
        for subject in sorted(
            logical_policy.subjects,
            key=lambda item: (item.type.value, item.name),
        ):
            exists = (
                client.user_exists(subject.name)
                if subject.type.value == "USER"
                else client.group_exists(subject.name)
            )
            if not exists:
                missing.append({"type": subject.type.value, "name": subject.name})
        if missing:
            raise ValidationError(
                "one or more Ranger policy subjects do not exist",
                details={"missing_subjects": missing},
            )

    def _ranger(self) -> RangerClient:
        if self.ranger_client is None:
            raise ValidationError(
                "Ranger client is required for policy preview/activation"
            )
        return self.ranger_client

    @staticmethod
    def _logical_policy(
        value: LogicalDataAccessPolicy | dict[str, Any],
    ) -> LogicalDataAccessPolicy:
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
    def _policy_key(value: str) -> str:
        try:
            return normalize_policy_key(value)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
