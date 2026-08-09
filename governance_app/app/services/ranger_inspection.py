"""Service for inspecting Apache Ranger policies and tag definitions.

Extracts/reuses Ranger client instantiation logic to provide a clean service layer
for FastMCP tools and control plane diagnostics without duplicating integration logic.
"""
from __future__ import annotations

import logging
from typing import Any

from app.clients.ranger import RangerClient
from app.clients.ranger_tags import RangerTagStoreClient
from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


def create_ranger_policy_client(
    settings: Settings,
    service_name: str | None = None,
) -> RangerClient:
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


def create_ranger_tag_store_client(
    settings: Settings,
) -> RangerTagStoreClient:
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
    """Shared application service for read-only inspection of Ranger policies and tags."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def inspect(self, service_name: str | None = None) -> dict[str, Any]:
        """Inspect Ranger policies and tag definitions.

        Raises ExternalSystemError if Ranger calls fail.
        """
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
                    "id": p.get("id"),
                    "name": p.get("name"),
                    "isEnabled": p.get("isEnabled"),
                    "resources": p.get("resources"),
                }
                for p in policies[:20]
            ],
            "tag_definitions": [t.get("name") for t in tag_defs if isinstance(t, dict) and t.get("name")],
        }
