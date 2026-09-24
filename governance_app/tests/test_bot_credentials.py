import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings


def test_agent_and_execution_openmetadata_bots_must_be_distinct() -> None:
    with pytest.raises(
        ValidationError,
        match="must be different machine identities",
    ):
        Settings(
            openmetadata_execution_bot_name="same-bot",
            openmetadata_agent_bot_name="same-bot",
        )


def test_default_runtime_identities_are_machine_bots() -> None:
    settings = Settings()
    assert settings.openmetadata_execution_bot_name.endswith("-bot")
    assert settings.openmetadata_agent_bot_name.endswith("-bot")


def test_remaining_openmetadata_worker_tokens_must_be_distinct() -> None:
    with pytest.raises(ValidationError, match="must be different"):
        Settings(
            OM_AUTO_TAG_BOT_TOKEN=SecretStr("same-token"),
            OM_INGESTION_BOT_TOKEN=SecretStr("same-token"),
        )
