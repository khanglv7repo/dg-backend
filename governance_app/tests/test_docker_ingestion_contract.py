from pathlib import Path


def test_docker_ingestion_uses_ingestion_bot_without_admin_fallback() -> None:
    runner = (Path(__file__).parents[1] / "docker" / "metadata-ingestion" / "runner.py").read_text()

    assert "OM_INGESTION_BOT_TOKEN" in runner
    assert "OPENMETADATA_ADMIN_TOKEN" not in runner
    assert "OPENMETADATA_JWT_TOKEN" not in runner
    assert "users/login" not in runner


def test_docker_ingestion_compose_does_not_inject_admin_token() -> None:
    compose = (Path(__file__).parents[1] / "docker-compose.ingestion.yml").read_text()

    assert "OM_INGESTION_BOT_TOKEN" in compose
    assert "OPENMETADATA_ADMIN_TOKEN" not in compose
