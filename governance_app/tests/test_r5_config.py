from __future__ import annotations

from app.core.config import Settings


def test_r5_mcp_and_trino_defaults_are_separate_and_bounded() -> None:
    settings = Settings(app_env="test")
    assert settings.mcp_host == "127.0.0.1"
    assert settings.mcp_port == 8001
    assert settings.mcp_path == "/mcp"
    assert settings.mcp_enabled is False
    assert settings.trino_readonly_max_rows <= 1000
    assert settings.trino_readonly_max_response_bytes <= 2_097_152


def test_legacy_trino_names_remain_accepted() -> None:
    settings = Settings.model_validate(
        {
            "app_env": "test",
            "TRINO_ENABLED": True,
            "TRINO_HOST": "trino.local",
            "TRINO_PORT": 8088,
            "TRINO_CATALOG": "financial",
            "TRINO_SCHEMA": "crm",
            "TRINO_VERIFICATION_SERVICE_USER": "diagnostic-reader",
            "TRINO_HTTP_SCHEME": "http",
            "TRINO_TIMEOUT_SECONDS": 17,
        }
    )
    assert settings.trino_readonly_enabled is True
    assert settings.trino_readonly_host == "trino.local"
    assert settings.trino_readonly_port == 8088
    assert settings.trino_readonly_catalog == "financial"
    assert settings.trino_readonly_schema == "crm"
    assert settings.trino_readonly_user == "diagnostic-reader"
    assert settings.trino_readonly_timeout_seconds == 17
