from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ConfigurationError, ValidationError
from app.repositories.audit import AuditRepository
from app.repositories.policies import PolicyRepository
from app.rules.policy_mapping import PolicyMappingResolver
from app.schemas.policy_catalog import RangerPolicyDocument


class PolicyCatalogService:
    """Own the backend desired-state policy catalog stored in PostgreSQL."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.repository = PolicyRepository(session)
        self.audit = AuditRepository(session)

    def list_policies(self):
        return self.repository.list_all()

    def get_policy(self, policy_id):
        return self.repository.get(policy_id)

    def import_document(
        self,
        document: dict[str, Any],
        *,
        actor_id: str,
        actor_name: str,
        correlation_id: str | None = None,
    ):
        try:
            parsed = RangerPolicyDocument.model_validate(document)
        except PydanticValidationError as exc:
            raise ValidationError(
                "invalid native Ranger policy JSON",
                details={"errors": exc.errors(include_url=False)},
            ) from exc
        native = parsed.native_document()
        service = str(native["service"])
        name = str(native["name"])
        policy_kind = self._policy_kind(native)
        service_type = (
            str(native.get("serviceType"))
            if native.get("serviceType") is not None
            else None
        )
        policy_key = f"{service}:{name}"
        enabled = bool(native.get("isEnabled", True))

        policy, created, changed = self.repository.upsert(
            policy_key=policy_key,
            policy_kind=policy_kind,
            service=service,
            service_type=service_type,
            name=name,
            document=native,
            enabled=enabled,
        )
        self.audit.record(
            actor_id=actor_id,
            actor_name=actor_name,
            action=(
                "POLICY_IMPORTED"
                if created
                else "POLICY_UPDATED" if changed else "POLICY_IMPORT_NO_CHANGE"
            ),
            object_type="policy",
            object_id=str(policy.id),
            correlation_id=correlation_id,
            details={
                "policy_key": policy.policy_key,
                "policy_kind": policy.policy_kind,
                "service": policy.service,
                "name": policy.name,
                "revision": policy.revision,
            },
        )
        return policy, created, changed

    def disable(
        self,
        policy_id,
        *,
        actor_id: str,
        actor_name: str,
        correlation_id: str | None = None,
    ):
        policy = self.repository.disable(policy_id)
        self.audit.record(
            actor_id=actor_id,
            actor_name=actor_name,
            action="POLICY_DISABLED",
            object_type="policy",
            object_id=str(policy.id),
            correlation_id=correlation_id,
            details={
                "policy_key": policy.policy_key,
                "revision": policy.revision,
            },
        )
        return policy

    def seed_legacy_catalog_if_empty(self) -> int:
        """One-time compatibility seed from the old YAML tag-policy catalog.

        PostgreSQL remains the source of truth after the first seed. The YAML is
        never consulted by the normal DB-backed reconciler.
        """

        if self.repository.count() > 0:
            return 0
        path: Path = self.settings.resolve_path(self.settings.policy_mappings_path)
        if not path.exists():
            return 0

        resolver = PolicyMappingResolver.from_path(path)
        desired = resolver.resolve_all(service=self.settings.ranger_tag_service_name)
        seeded = 0
        for policy in desired:
            document = policy.ranger_document()
            document.update(
                {
                    "serviceType": "tag",
                    "policyType": 0,
                    "policyPriority": 0,
                    "isAuditEnabled": True,
                    "isDenyAllElse": False,
                }
            )
            _, created, _ = self.import_document(
                document,
                actor_id="system:legacy-policy-seed",
                actor_name="Legacy Policy Seed",
            )
            seeded += int(created)
        return seeded

    def _policy_kind(self, document: dict[str, Any]) -> str:
        service = str(document.get("service") or "")
        service_type = str(document.get("serviceType") or "")
        resources = set((document.get("resources") or {}).keys())

        allowed_services = {
            self.settings.ranger_tag_service_name,
            self.settings.ranger_service_name,
        }
        if service not in allowed_services:
            raise ConfigurationError(
                f"policy targets unsupported Ranger service {service!r}; expected one of "
                f"{sorted(allowed_services)}"
            )

        tag_signals = (
            service == self.settings.ranger_tag_service_name,
            service_type.lower() == "tag",
            "tag" in resources,
        )
        is_tag = any(tag_signals)
        if is_tag:
            if service != self.settings.ranger_tag_service_name:
                raise ConfigurationError(
                    "tag policies must target RANGER_TAG_SERVICE_NAME"
                )
            if resources != {"tag"}:
                raise ConfigurationError(
                    "tag policies must contain only the Ranger 'tag' resource"
                )
            return "TAG"

        if service != self.settings.ranger_service_name:
            raise ConfigurationError(
                "resource policies must target RANGER_RESOURCE_SERVICE_NAME"
            )
        if "tag" in resources:
            raise ConfigurationError(
                "resource policies must not contain the Ranger 'tag' resource"
            )
        return "RESOURCE"
