import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.jobs.handlers import (
    _auto_tag_openmetadata_client,
    _autoclassification_openmetadata_client,
    _ingestion_openmetadata_client,
)


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


def test_execution_worker_routes_each_openmetadata_role_to_its_own_token() -> None:
    settings = Settings(
        openmetadata_enabled=True,
        OM_AUTOCLASSIFICATION_BOT_TOKEN=SecretStr("autoclassification-token"),
        OM_AUTO_TAG_BOT_TOKEN=SecretStr("auto-tag-token"),
        OM_INGESTION_BOT_TOKEN=SecretStr("ingestion-token"),
    )

    autoclassification = _autoclassification_openmetadata_client(settings)
    auto_tag = _auto_tag_openmetadata_client(settings)
    ingestion = _ingestion_openmetadata_client(settings)
    try:
        assert (
            autoclassification.client.headers["Authorization"]
            == "Bearer autoclassification-token"
        )
        assert (
            auto_tag.client.headers["Authorization"]
            == "Bearer auto-tag-token"
        )
        assert (
            ingestion.client.headers["Authorization"]
            == "Bearer ingestion-token"
        )
    finally:
        autoclassification.close()
        auto_tag.close()
        ingestion.close()


def test_openmetadata_worker_bot_tokens_must_be_distinct() -> None:
    with pytest.raises(ValidationError, match="must be different"):
        Settings(
            OM_AUTOCLASSIFICATION_BOT_TOKEN=SecretStr("same-token"),
            OM_AUTO_TAG_BOT_TOKEN=SecretStr("same-token"),
        )
