from __future__ import annotations

from unittest.mock import MagicMock, patch

import anyio
import pytest
from fastmcp import Client

from app.core.errors import ExternalSystemError, ValidationError
from app.mcp.backend_mcp_server import mcp
from app.services.ranger_inspection import RangerInspectionService
from app.services.trino_diagnostic import TrinoDiagnosticService, validate_read_only_query


def test_ingestion_runner_has_no_internal_periodic_scheduler() -> None:
    from pathlib import Path

    runner_path = (
        Path(__file__).parents[3]
        / "infrastructure"
        / "docker"
        / "metadata-ingestion"
        / "runner.py"
    )
    content = runner_path.read_text(encoding="utf-8")

    assert "def start_scheduler" not in content
    assert "def scheduler_loop" not in content
    assert "/run-now" in content
    assert "/health" in content
    assert "/status" in content
    assert "_run_lock" in content


def test_ranger_inspection_service_calls_correct_adapter_contracts() -> None:
    with patch(
        "app.services.ranger_inspection.create_ranger_policy_client"
    ) as mock_policy_factory, patch(
        "app.services.ranger_inspection.create_ranger_tag_store_client"
    ) as mock_tag_factory:
        mock_policy_client = MagicMock()
        mock_policy_client.list_policies.return_value = [
            {"id": "1", "name": "p1", "isEnabled": True, "resources": {}}
        ]
        mock_policy_factory.return_value = mock_policy_client

        mock_tag_client = MagicMock()
        mock_tag_client.list_tag_definitions.return_value = [{"name": "PII.Phone"}]
        mock_tag_factory.return_value = mock_tag_client

        service = RangerInspectionService()
        result = service.inspect(service_name="dev_trino")

        assert result["service_name"] == "dev_trino"
        assert result["policies_count"] == 1
        assert result["tag_definitions"] == ["PII.Phone"]
        mock_policy_client.list_policies.assert_called_once()
        mock_tag_client.list_tag_definitions.assert_called_once()


def test_ranger_inspection_service_raises_on_broken_wiring() -> None:
    with patch(
        "app.services.ranger_inspection.create_ranger_policy_client"
    ) as mock_policy_factory:
        mock_policy_client = MagicMock()
        mock_policy_client.list_policies.side_effect = ExternalSystemError(
            "Ranger down",
            system="ranger",
        )
        mock_policy_factory.return_value = mock_policy_client

        service = RangerInspectionService()
        with pytest.raises(ExternalSystemError, match="Ranger down"):
            service.inspect(service_name="dev_trino")


def test_trino_query_validation_rejects_multi_statement() -> None:
    with pytest.raises(ValidationError, match="Multi-statement"):
        validate_read_only_query("SELECT 1; DROP TABLE customer;")


def test_trino_query_validation_rejects_ddl_dml() -> None:
    for sql in [
        "INSERT INTO customer VALUES (1)",
        "UPDATE customer SET phone='123'",
        "DELETE FROM customer",
        "DROP TABLE customer",
        "CREATE TABLE test (id int)",
        "ALTER TABLE customer ADD COLUMN foo text",
        "GRANT SELECT ON customer TO bob",
        "TRUNCATE TABLE customer",
    ]:
        with pytest.raises(ValidationError):
            validate_read_only_query(sql)


def test_trino_diagnostic_service_does_not_limit_show_describe_explain() -> None:
    _service = TrinoDiagnosticService()

    query, verb = validate_read_only_query("SHOW TABLES")
    assert query == "SHOW TABLES"
    assert verb == "SHOW"

    query, verb = validate_read_only_query("DESCRIBE customer")
    assert query == "DESCRIBE customer"
    assert verb == "DESCRIBE"

    query, verb = validate_read_only_query("EXPLAIN SELECT * FROM customer")
    assert query == "EXPLAIN SELECT * FROM customer"
    assert verb == "EXPLAIN"

    query_select, verb_select = validate_read_only_query("SELECT * FROM customer")
    assert query_select == "SELECT * FROM customer"
    assert verb_select == "SELECT"


def test_mcp_policy_placeholders_removed_from_backend_server() -> None:
    async def run() -> None:
        async with Client(mcp) as client:
            tools = [tool.name for tool in await client.list_tools()]
            assert "inspect_ranger_state" in tools
            assert "query_trino_readonly" in tools
            assert "inspect_policy_state" not in tools
            assert "submit_policy_command" not in tools

    anyio.run(run)
