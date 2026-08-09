from __future__ import annotations

from app.mcp.backend_mcp_server import inspect_ranger_state, query_trino_readonly


def test_inspect_ranger_state_handles_missing_connection() -> None:
    result = inspect_ranger_state(service_name="dev_trino")
    assert "service_name" in result
    assert result["service_name"] == "dev_trino"


def test_query_trino_readonly_rejects_non_select() -> None:
    result = query_trino_readonly(query="DROP TABLE customer", username="alice")
    assert "error" in result
    assert "read-only" in result["error"]
