from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.models.service_mapping import ServiceMapping


class ServiceMappingRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_exact(
        self,
        *,
        om_service_name: str,
        environment: str,
    ) -> ServiceMapping | None:
        return self.session.scalar(
            select(ServiceMapping).where(
                ServiceMapping.om_service_name == om_service_name,
                ServiceMapping.environment == environment,
            )
        )

    def upsert(
        self,
        *,
        om_service_name: str,
        trino_catalog: str,
        ranger_service_name: str,
        ranger_tag_service_name: str | None,
        environment: str,
        enabled: bool,
        actor_id: str,
    ) -> tuple[ServiceMapping, bool]:
        current = self.get_exact(
            om_service_name=om_service_name,
            environment=environment,
        )
        created = current is None
        if current is None:
            current = ServiceMapping(
                om_service_name=om_service_name,
                trino_catalog=trino_catalog,
                ranger_service_name=ranger_service_name,
                ranger_tag_service_name=ranger_tag_service_name,
                environment=environment,
                enabled=enabled,
                updated_by=actor_id,
            )
            self.session.add(current)
        else:
            current.trino_catalog = trino_catalog
            current.ranger_service_name = ranger_service_name
            current.ranger_tag_service_name = ranger_tag_service_name
            current.enabled = enabled
            current.updated_by = actor_id
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                "service mapping conflicts with existing om_service_name/environment"
            ) from exc
        return current, created
