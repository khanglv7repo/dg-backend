"""Read-only Apache Ranger inspection services.

R5 preserves the existing aggregate inspection adapter while adding bounded
policy/subject diagnostics used by the Backend MCP server. Both paths are
read-only and reuse the production Ranger clients.
"""
from __future__ import annotations

from typing import Any, Literal

from sqlalchemy.orm import Session

from app.clients.ranger import RangerClient
from app.clients.ranger_tags import RangerTagStoreClient
from app.core.config import Settings, get_settings
from app.core.errors import ConfigurationError, NotFoundError, ValidationError
from app.repositories.data_access_policy import DataAccessPolicyRepository
from app.schemas.data_access_policy import normalize_policy_key

InspectionKind = Literal["health", "policy", "policy_key", "user", "group"]
_MANAGED_MARKER = "managed-by=dg-backend;"
_HEALTH_STATE_FIELDS = (
    "id",
    "name",
    "type",
    "isEnabled",
    "tagService",
    "policyVersion",
    "policyUpdateTime",
    "tagVersion",
    "tagUpdateTime",
)


def create_ranger_policy_client(
    settings: Settings,
    service_name: str | None = None,
) -> RangerClient:
    """Create the existing resource-policy Ranger client adapter."""

    target_service = service_name or settings.ranger_service_name
    password = (
        settings.ranger_service_secret.get_secret_value()
        if settings.ranger_service_secret
        else None
    )
    return RangerClient(
        base_url=settings.ranger_base_url,
        username=settings.ranger_service_account,
        password=password,
        service_name=target_service,
        dry_run=settings.ranger_dry_run,
        timeout=settings.ranger_timeout_seconds,
    )


def create_ranger_tag_store_client(settings: Settings) -> RangerTagStoreClient:
    """Create the existing Ranger tag-store inspection adapter."""

    password = (
        settings.ranger_service_secret.get_secret_value()
        if settings.ranger_service_secret
        else None
    )
    return RangerTagStoreClient(
        base_url=settings.ranger_tag_store_base_url,
        username=settings.ranger_service_account,
        password=password,
        resource_service_name=settings.ranger_service_name,
        dry_run=settings.ranger_dry_run,
        timeout=settings.ranger_timeout_seconds,
    )


class RangerInspectionService:
    """Shared application service for bounded, read-only Ranger inspection."""

    def __init__(
        self,
        session: Session | None = None,
        *,
        ranger_client: RangerClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.ranger = ranger_client
        self.policies = (
            DataAccessPolicyRepository(session) if session is not None else None
        )

    def inspect(
        self,
        service_name: str | None = None,
        *,
        kind: InspectionKind | None = None,
        name: str | None = None,
        policy_key: str | None = None,
    ) -> dict[str, Any]:
        """Run aggregate legacy inspection or one bounded R5 diagnostic read.

        ``kind=None`` preserves the pre-R5 aggregate inspection contract used by
        existing Backend diagnostics. R5 MCP calls always pass an explicit kind.
        """

        if kind is None:
            return self._inspect_aggregate(service_name=service_name)

        ranger, owned_client = self._resource_client(service_name=service_name)
        try:
            if kind == "health":
                return self._health(ranger=ranger)
            if kind == "policy":
                return self._policy(
                    ranger=ranger,
                    name=name,
                    policy_key=policy_key,
                )
            if kind == "policy_key":
                return self._policy_key(ranger=ranger, policy_key=policy_key)
            if kind == "user":
                return self._subject(ranger=ranger, subject_type="USER", name=name)
            if kind == "group":
                return self._subject(ranger=ranger, subject_type="GROUP", name=name)
            raise ValidationError(f"unsupported Ranger inspection kind {kind!r}")
        finally:
            if owned_client:
                ranger.close()


    @staticmethod
    def _health(*, ranger: RangerClient) -> dict[str, Any]:
        """Return a bounded Ranger service-health projection.

        Ranger's service endpoint can include connection configs and account
        metadata. MCP diagnostics expose only operational version/identity fields
        required to determine whether the configured service is reachable/current.
        """

        raw = ranger.health()
        state = (
            {key: raw.get(key) for key in _HEALTH_STATE_FIELDS if key in raw}
            if isinstance(raw, dict)
            else {}
        )
        return {
            "kind": "health",
            "service_name": ranger.service_name,
            "state": state,
        }

    def _inspect_aggregate(self, *, service_name: str | None) -> dict[str, Any]:
        target_service = service_name or self.settings.ranger_service_name

        policy_client = create_ranger_policy_client(self.settings, target_service)
        try:
            policies = policy_client.list_policies()
        finally:
            policy_client.close()

        tag_client = create_ranger_tag_store_client(self.settings)
        try:
            tag_defs = tag_client.list_tag_definitions()
        finally:
            tag_client.close()

        return {
            "service_name": target_service,
            "tag_service_name": self.settings.ranger_tag_service_name,
            "policies_count": len(policies),
            "policies": [
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "isEnabled": item.get("isEnabled"),
                    "resources": item.get("resources"),
                }
                for item in policies[:20]
                if isinstance(item, dict)
            ],
            "tag_definitions": [
                item.get("name")
                for item in tag_defs
                if isinstance(item, dict) and item.get("name")
            ],
        }

    def _resource_client(
        self,
        *,
        service_name: str | None,
    ) -> tuple[RangerClient, bool]:
        if self.ranger is not None:
            return self.ranger, False
        return create_ranger_policy_client(self.settings, service_name), True

    def _policy(
        self,
        *,
        ranger: RangerClient,
        name: str | None,
        policy_key: str | None,
    ) -> dict[str, Any]:
        exact_name = self._required(name, "name")
        current = ranger.find_by_name(exact_name)
        if current is None:
            raise NotFoundError(f"Ranger policy {exact_name!r} was not found")

        description = str(current.get("description") or "")
        result: dict[str, Any] = {
            "kind": "policy",
            "name": exact_name,
            "found": True,
            "managed_by_backend": _MANAGED_MARKER in description,
            "policy_type": current.get("policyType"),
            "id": str(current.get("id")) if current.get("id") is not None else None,
            "guid": current.get("guid"),
            "service": current.get("service"),
            "is_enabled": current.get("isEnabled"),
            "resources": current.get("resources"),
        }
        if policy_key is not None:
            key = self._policy_key_value(policy_key)
            result["owned_for_policy_key"] = ranger.owns_policy(
                current,
                policy_key=key,
            )
        return result

    def _policy_key(
        self,
        *,
        ranger: RangerClient,
        policy_key: str | None,
    ) -> dict[str, Any]:
        if self.policies is None:
            raise ConfigurationError(
                "Backend database session is required for policy_key Ranger inspection"
            )
        key = self._policy_key_value(self._required(policy_key, "policy_key"))
        names = sorted(self.policies.projection_names_for_policy_key(key))
        states: list[dict[str, Any]] = []
        for policy_name in names:
            current = ranger.find_by_name(policy_name)
            states.append(
                {
                    "name": policy_name,
                    "found": current is not None,
                    "owned": ranger.owns_policy(current, policy_key=key),
                    "policy_type": current.get("policyType") if current else None,
                    "id": (
                        str(current.get("id"))
                        if current and current.get("id") is not None
                        else None
                    ),
                    "is_enabled": current.get("isEnabled") if current else None,
                }
            )
        return {
            "kind": "policy_key",
            "policy_key": key,
            "projection_count": len(names),
            "policies": states,
        }

    def _subject(
        self,
        *,
        ranger: RangerClient,
        subject_type: str,
        name: str | None,
    ) -> dict[str, Any]:
        exact_name = self._required(name, "name")
        current = (
            ranger.find_user(exact_name)
            if subject_type == "USER"
            else ranger.find_group(exact_name)
        )
        return {
            "kind": subject_type.lower(),
            "name": exact_name,
            "exists": current is not None,
            "id": (
                str(current.get("id"))
                if current and current.get("id") is not None
                else None
            ),
        }

    @staticmethod
    def _required(value: str | None, field: str) -> str:
        if value is None or not value.strip():
            raise ValidationError(f"{field} is required for this Ranger inspection")
        return value.strip()

    @staticmethod
    def _policy_key_value(value: str) -> str:
        try:
            return normalize_policy_key(value)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
