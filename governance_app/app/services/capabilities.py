from __future__ import annotations

from app.core.config import Settings


class CapabilityService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def report(self) -> dict:
        return {
            "application": {
                "topology": "one FastAPI control API plus execution and agent worker roles",
                "architecture": "MVC + Service + Repository",
                "queue": "PostgreSQL governance_jobs",
            },
            "openmetadata": {
                "enabled": self.settings.openmetadata_enabled,
                "native_suggestions": True,
                "targeted_column_updates": True,
                "raw_change_event_adapter": True,
                "watermark_asset_discovery": True,
                "bounded_sample_value_scanner": self.settings.sample_scan_enabled,
                "tag_removal_reconciliation": True,
                "trusted_auto_apply_enabled": self.settings.trusted_auto_apply_enabled,
                "autoclassification_bot": self.settings.openmetadata_execution_bot_name,
                "auto_tag_bot_configured": bool(self.settings.openmetadata_auto_tag_bot_token),
                "ingestion_bot_configured": bool(self.settings.openmetadata_ingestion_bot_token),
                "live_contract_verified": False,
            },
            "agent_worker": {
                "enabled": self.settings.agent_enabled,
                "orchestrator": "LangGraph",
                "catalog_interface": "OpenMetadata MCP read-only tools",
                "openmetadata_bot": self.settings.openmetadata_agent_bot_name,
                "writes": "none; result is persisted and execution worker creates native Suggestions",
                "direct_ranger_or_trino_access": False,
                "separate_fastapi_application": False,
            },
            "ranger": {
                "enabled": self.settings.ranger_enabled,
                "dry_run": self.settings.ranger_dry_run,
                "service_name": self.settings.ranger_service_name,
                "service_account": self.settings.ranger_service_account,
            },
            "trino": {
                "enabled": self.settings.trino_enabled,
                "verification_service_user": self.settings.trino_verification_service_user,
            },
            "identity_rule": "runtime components use machine Bots/service identities, never personal accounts",
            "custom_approval_store": False,
        }
