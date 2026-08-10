from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, ValidationError
from app.repositories.audit import AuditRepository
from app.repositories.service_mapping import ServiceMappingRepository


class ServiceMappingService:
    """Exact service-level mapping from the accepted Backend data contract."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = ServiceMappingRepository(session)
        self.audit = AuditRepository(session)

    def resolve(
        self,
        *,
        om_service_name: str,
        environment: str,
    ) -> dict:
        service = self._required(om_service_name, "om_service_name", 255)
        env = self._required(environment, "environment", 64)
        mapping = self.repository.get_exact(
            om_service_name=service,
            environment=env,
        )
        if mapping is None or not mapping.enabled:
            raise NotFoundError(
                f"service mapping was not resolved for {service!r} in {env!r}",
                details={
                    "status": "UNRESOLVED",
                    "om_service_name": service,
                    "environment": env,
                },
            )
        return self._document(mapping)

    def update(
        self,
        *,
        om_service_name: str,
        trino_catalog: str,
        ranger_service_name: str,
        ranger_tag_service_name: str | None,
        environment: str,
        enabled: bool,
        actor_id: str,
        actor_name: str,
        reason: str | None = None,
    ) -> dict:
        service = self._required(om_service_name, "om_service_name", 255)
        catalog = self._required(trino_catalog, "trino_catalog", 255)
        ranger_service = self._required(
            ranger_service_name,
            "ranger_service_name",
            255,
        )
        ranger_tag = self._optional(ranger_tag_service_name, 255)
        env = self._required(environment, "environment", 64)
        actor = self._required(actor_id, "actor_id", 255)
        actor_display = self._required(actor_name, "actor_name", 255)

        mapping, created = self.repository.upsert(
            om_service_name=service,
            trino_catalog=catalog,
            ranger_service_name=ranger_service,
            ranger_tag_service_name=ranger_tag,
            environment=env,
            enabled=bool(enabled),
            actor_id=actor,
        )
        self.audit.record(
            actor_id=actor,
            actor_name=actor_display,
            action=("SERVICE_MAPPING_CREATED" if created else "SERVICE_MAPPING_UPDATED"),
            object_type="service-mapping",
            object_id=str(mapping.id),
            correlation_id=None,
            details={
                "om_service_name": service,
                "trino_catalog": catalog,
                "ranger_service_name": ranger_service,
                "ranger_tag_service_name": ranger_tag,
                "environment": env,
                "enabled": bool(enabled),
                "reason": self._optional(reason, 1000),
            },
        )
        return self._document(mapping)

    @staticmethod
    def _document(mapping) -> dict:
        return {
            "status": "RESOLVED" if mapping.enabled else "DISABLED",
            "id": str(mapping.id),
            "om_service_name": mapping.om_service_name,
            "trino_catalog": mapping.trino_catalog,
            "ranger_service_name": mapping.ranger_service_name,
            "ranger_tag_service_name": mapping.ranger_tag_service_name,
            "environment": mapping.environment,
            "enabled": mapping.enabled,
            "created_at": mapping.created_at,
            "updated_at": mapping.updated_at,
            "updated_by": mapping.updated_by,
        }

    @staticmethod
    def _required(value: str, field: str, limit: int) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValidationError(f"{field} must not be empty")
        if len(normalized) > limit:
            raise ValidationError(f"{field} exceeds {limit} characters")
        return normalized

    @staticmethod
    def _optional(value: str | None, limit: int) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        if not normalized:
            return None
        if len(normalized) > limit:
            raise ValidationError(f"value exceeds {limit} characters")
        return normalized
