from __future__ import annotations

from app.clients.ranger import RangerClient
from app.core.config import Settings
from app.core.errors import ConfigurationError


def build_resource_ranger_client(settings: Settings) -> RangerClient:
    if not settings.ranger_enabled:
        raise ConfigurationError("Ranger must be enabled for data-access policy activation")
    return RangerClient(
        base_url=settings.ranger_base_url,
        username=settings.ranger_service_account,
        password=(
            settings.ranger_service_secret.get_secret_value()
            if settings.ranger_service_secret
            else None
        ),
        service_name=settings.ranger_service_name,
        dry_run=settings.ranger_dry_run,
        timeout=settings.ranger_timeout_seconds,
    )
