from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the FastAPI API and execution worker.

    External credentials are machine identities. Runtime components must not
    use personal OpenMetadata or Ranger accounts.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    app_env: Literal["local", "test", "staging", "production"] = "local"
    app_log_level: str = "INFO"
    api_prefix: str = "/api/v1"
    database_url: str = "sqlite+pysqlite:///./governance.db"

    # OpenMetadata identities used by backend workflows.
    openmetadata_enabled: bool = False
    openmetadata_base_url: str = "http://localhost:8585/api"
    openmetadata_execution_bot_name: str = "governance-execution-bot"
    openmetadata_execution_bot_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "OM_AUTOCLASSIFICATION_BOT_TOKEN",
            "OPENMETADATA_EXECUTION_BOT_TOKEN",
        ),
    )
    openmetadata_auto_tag_bot_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("OM_AUTO_TAG_BOT_TOKEN"),
    )
    openmetadata_ingestion_bot_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("OM_INGESTION_BOT_TOKEN"),
    )
    openmetadata_timeout_seconds: float = 15.0
    trusted_auto_apply_enabled: bool = False

    # Optional AI/Agent configuration is retained outside the core policy path.
    # It can be removed in a separate cleanup if the standalone agent is retired.
    agent_enabled: bool = False
    agent_name: str = "classification-agent"
    agent_graph_version: str = "classification-graph-v1"
    agent_prompt_version: str = "classification-prompt-v1"
    openmetadata_agent_bot_name: str = "governance-agent-bot"
    openmetadata_mcp_url: str = "http://localhost:8585/mcp"
    openmetadata_agent_bot_token: SecretStr | None = None
    openmetadata_mcp_timeout_seconds: float = 30.0
    agent_include_lineage: bool = True
    llm_provider: str = "openai"
    llm_model: str = "gpt-5-mini"
    llm_api_key: SecretStr | None = None

    # Ranger machine/service identity.
    ranger_enabled: bool = False
    ranger_base_url: str = "http://localhost:6080/service/public/v2/api"
    ranger_tag_store_base_url: str = "http://localhost:6080/service/tags"
    ranger_service_account: str | None = None
    ranger_service_secret: SecretStr | None = None

    # Resource service receiving Ranger resource/tag assignments.
    # RANGER_SERVICE_NAME remains supported so the existing local .env does not break.
    ranger_service_name: str = Field(
        default="trino",
        validation_alias=AliasChoices(
            "RANGER_RESOURCE_SERVICE_NAME",
            "RANGER_SERVICE_NAME",
        ),
    )
    ranger_tag_service_name: str = "dev_tag"
    ranger_dry_run: bool = True
    ranger_timeout_seconds: float = 15.0

    # OpenMetadata webhook integration secret.
    openmetadata_webhook_secret: SecretStr | None = None


    classification_rules_path: Path = Path("config/classification_rules.yaml")

    auto_start_execution_worker: bool = True
    execution_worker_id: str = "execution-worker-1"
    agent_worker_id: str = "agent-worker-1"
    worker_poll_seconds: float = 1.0
    worker_claim_batch: int = Field(default=8, ge=1, le=100)
    worker_stale_after_seconds: int = Field(default=300, ge=30)

    # Local/development deployments may trust identity headers from the caller.
    # Missing headers never grant a role. Set false when the API is not behind a
    # trusted identity-aware proxy/gateway.
    trusted_identity_headers: bool = True

    @field_validator("api_prefix")
    @classmethod
    def validate_prefix(cls, value: str) -> str:
        return "/" + value.strip("/")

    @model_validator(mode="after")
    def validate_machine_identity_separation(self) -> "Settings":
        if self.openmetadata_execution_bot_name == self.openmetadata_agent_bot_name:
            raise ValueError(
                "OpenMetadata execution and agent bots must be different machine identities"
            )
        worker_tokens = [
            token.get_secret_value()
            for token in (
                self.openmetadata_execution_bot_token,
                self.openmetadata_auto_tag_bot_token,
                self.openmetadata_ingestion_bot_token,
            )
            if token
        ]
        if len(worker_tokens) != len(set(worker_tokens)):
            raise ValueError(
                "OpenMetadata ingestion, auto-classification, and auto-tag bot tokens "
                "must be different"
            )
        return self

    def resolve_path(self, value: Path) -> Path:
        if value.is_absolute():
            return value
        return Path.cwd() / value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
