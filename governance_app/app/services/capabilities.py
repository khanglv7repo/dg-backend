from __future__ import annotations

from app.core.config import Settings


class CapabilityService:
    """Describe the active architecture, not retired compatibility paths."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def report(self) -> dict:
        return {
            "application": {
                "topology": "FastAPI control API + Celery workers + PostgreSQL state",
                "async_execution": "Celery over Redis with durable Inbox/Outbox coordination",
                "policy_source_of_truth": "PostgreSQL data_access_policy_version",
                "policy_runtime_projection": "Apache Ranger",
                "legacy_governance_job_queue": False,
                "legacy_native_ranger_policy_catalog": False,
            },
            "openmetadata": {
                "enabled": self.settings.openmetadata_enabled,
                "version_target": "2.0.2",
                "metadata_source_of_truth": True,
                "tag_source_of_truth": True,
                "lineage_source_of_truth": True,
                "dq_materialized_state_source_of_truth": True,
                "backend_direct_tag_mutation": False,
                "raw_change_event_adapter": True,
                "execution_bot": self.settings.openmetadata_execution_bot_name,
                "execution_bot_configured": bool(
                    self.settings.openmetadata_execution_bot_token
                ),
                "ingestion_bot_configured": bool(
                    self.settings.openmetadata_ingestion_bot_token
                ),
            },
            "policy": {
                "logical_policy_versions": True,
                "draft_via_mcp": bool(self.settings.mcp_enabled),
                "activation_via_mcp": False,
                "compiler": "logical-policy -> deterministic Ranger projections",
                "transactional_outbox": True,
                "ranger_readback": True,
            },
            "ranger": {
                "enabled": self.settings.ranger_enabled,
                "dry_run": self.settings.ranger_dry_run,
                "resource_service": self.settings.ranger_service_name,
                "tag_service": self.settings.ranger_tag_service_name,
                "policy_reconciliation": "Celery desired-state reconciliation",
                "confirmed_tag_sync": "OpenMetadata read-back -> Ranger ServiceTags",
            },
            "data_quality": {
                "architecture": "Agent spec -> Backend STAGED -> human APPROVED -> OpenMetadata -> EXECUTABLE",
                "human_approval_required": True,
                "execution_semantics": "AT_LEAST_ONCE",
                "runner": self.settings.dq_runner_url,
            },
            "mcp": {
                "enabled": self.settings.mcp_enabled,
                "design": "bounded adapter over Backend application services",
                "direct_ranger_credentials": False,
                "authority_mutation_tools": False,
            },
            "trino_verification": {
                "enabled": self.settings.trino_readonly_enabled,
                "policy_user_configured": bool(self.settings.trino_readonly_user),
                "control_user_configured": bool(
                    self.settings.trino_verification_control_user
                ),
                "access": True,
                "mask_hash_character_columns": bool(
                    self.settings.trino_verification_control_user
                ),
                "row_filter": bool(self.settings.trino_verification_control_user),
                "insufficient_evidence_behavior": "VERIFICATION_UNAVAILABLE",
            },
            "identity_rule": (
                "missing identity headers grant no roles; authoritative mutations "
                "require trusted operator/admin or execution identities"
            ),
        }
