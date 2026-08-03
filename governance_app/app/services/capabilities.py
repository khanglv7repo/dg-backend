from __future__ import annotations

from app.core.config import Settings


class CapabilityService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def report(self) -> dict:
        return {
            "application": {
                "topology": "FastAPI control API plus PostgreSQL-backed execution worker",
                "architecture": "MVC + Service + Repository",
                "queue": "PostgreSQL governance_jobs",
                "policy_source_of_truth": "PostgreSQL governance_policies",
            },
            "openmetadata": {
                "enabled": self.settings.openmetadata_enabled,
                "metadata_source_of_truth": True,
                "tag_source_of_truth": True,
                "native_suggestions": True,
                "targeted_column_updates": True,
                "raw_change_event_adapter": True,
                "watermark_asset_discovery": True,
                "trusted_auto_apply_enabled": self.settings.trusted_auto_apply_enabled,
                "autoclassification_bot": self.settings.openmetadata_execution_bot_name,
                "auto_tag_bot_configured": bool(
                    self.settings.openmetadata_auto_tag_bot_token
                ),
                "ingestion_bot_configured": bool(
                    self.settings.openmetadata_ingestion_bot_token
                ),
            },
            "ranger": {
                "enabled": self.settings.ranger_enabled,
                "dry_run": self.settings.ranger_dry_run,
                "resource_service": self.settings.ranger_service_name,
                "tag_service": self.settings.ranger_tag_service_name,
                "policy_reconciliation": "explicit durable SYNC_RANGER_POLICIES job",
                "confirmed_tag_sync": "independent SYNC_RANGER_TAGS job",
            },
            "future_mcp": {
                "design": "thin adapter over backend application services",
                "direct_ranger_credentials": False,
            },
            "identity_rule": (
                "missing identity headers grant no roles; runtime mutations require "
                "machine/service identities or trusted operator/admin identity"
            ),
            "custom_approval_store": False,
            "trino_verification": False,
        }
